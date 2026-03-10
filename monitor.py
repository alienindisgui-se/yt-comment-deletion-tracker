import json
import os
import requests
import time
import datetime
import random
import sys
import logging
import hashlib
from playwright.sync_api import sync_playwright, TimeoutError

# Setup logging and encoding
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
VIDEO_LIST = "videos.json"
STATE_FILE = "comment_state.json"
WEBHOOK = os.getenv("DISCORD_WEBHOOK")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def generate_persistent_id(author, text):
    raw_str = f"{author}|{text}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

def to_unix(ts):
    if isinstance(ts, str):
        return datetime.datetime.fromisoformat(ts).timestamp()
    return ts

def migrate_timestamps(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ['ts_posted', 'created_at', 'lastSeen', 'last_checked'] and isinstance(value, (int, float)):
                obj[key] = datetime.datetime.fromtimestamp(value).isoformat()
            else:
                migrate_timestamps(value)
    elif isinstance(obj, list):
        for item in obj:
            migrate_timestamps(item)

def get_yt_data(v_id, deep_scrape=False):
    user_agent = random.choice(USER_AGENTS)
    logging.info(f"Selected user agent: {user_agent}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'}
        )
        page = context.new_page()
        
        try:
            logging.info(f"Opening video: {v_id}")
            page.goto(f"https://www.youtube.com/watch?v={v_id}", timeout=60000)
            logging.info("Page navigated successfully.")
            page.wait_for_load_state('networkidle')
            logging.info("Page loaded to networkidle state.")
            
            # Scroll to trigger the comment section
            page.evaluate("window.scrollBy(0, 800)")
            logging.info("Scrolled to trigger comments section.")
            
            try:
                page.wait_for_selector('ytd-comments#comments', state='attached', timeout=15000)
                page.wait_for_timeout(3000)
                logging.info("Comments section attached successfully.")
            except TimeoutError:
                logging.warning("Comments section did not attach in time. Video might have comments disabled.")
                return None, None, None

            # LANGUAGE-INDEPENDENT SORT TO "NEWEST FIRST"
            if deep_scrape:
                try:
                    page.evaluate("""() => {
                        const btn = document.querySelector('ytd-comments-header-renderer #sort-menu');
                        if (btn) btn.click();
                    }""")
                    page.wait_for_timeout(1000)
                    
                    page.evaluate("""() => {
                        const items = document.querySelectorAll('ytd-menu-service-item-renderer');
                        if (items.length > 1) items[1].click();
                    }""")
                    page.wait_for_timeout(3000)
                    logging.info("Sorted comments to 'Newest first' (language-independent).")
                except Exception as e:
                    logging.warning(f"Failed to sort comments: {e}. Proceeding with default sort.")

            # Get Title
            title_elem = page.locator('h1.ytd-watch-metadata yt-formatted-string')
            title = title_elem.text_content().strip() if title_elem.count() > 0 else 'Unknown'
            logging.info(f"Extracted video title: {title}")
            
            # Robust Count Extraction
            ui_count = 0
            count_locators = [
                ('#count .yt-core-attributed-string', 'yt-core-attributed-string'),
                ('h2#count yt-formatted-string', 'yt-formatted-string'),
                ('yt-formatted-string.count-text', 'count-text')
            ]
            
            for selector, desc in count_locators:
                try:
                    loc = page.locator(selector)
                    loc.wait_for(state='visible', timeout=10000)
                    count_text = loc.first.text_content().strip()
                    digits = ''.join(filter(str.isdigit, count_text))
                    if digits:
                        ui_count = int(digits)
                        logging.info(f"Extracted comment count using {desc}: {ui_count}")
                        break
                except TimeoutError:
                    logging.debug(f"Timeout waiting for count locator: {desc}")

            comments = {}
            if deep_scrape:
                logging.info(f"Starting deep scrape for '{title}'. UI reports ~{ui_count} comments.")
                
                # Phase 1: Load all top-level threads by scrolling
                logging.info("Loading all top-level comment threads...")
                last_thread_count = 0
                no_change = 0
                while True:
                    thread_nodes = page.locator('ytd-comment-thread-renderer')
                    current_thread = thread_nodes.count()
                    logging.info(f"Current top-level threads: {current_thread}")
                    if current_thread == last_thread_count:
                        no_change += 1
                        if no_change >= 3:
                            logging.info(f"Loaded {current_thread} top-level threads.")
                            break
                    else:
                        no_change = 0
                        last_thread_count = current_thread
                    page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                    logging.info("Scrolled to bottom for thread loading.")
                    page.wait_for_timeout(5000)

                # Phase 2: Expand replies using JavaScript to bypass actionability checks
                logging.info("Expanding nested replies via JavaScript injection...")
                max_iterations = 3  # Run a few times in case clicking reveals nested "show more" buttons
                
                for i in range(max_iterations):
                    try:
                        # Inject JS to find all reply buttons and click them directly in the DOM
                        clicks_dispatched = page.evaluate("""() => {
                            const buttons = Array.from(document.querySelectorAll('ytd-button-renderer#more-replies button'));
                            let count = 0;
                            for (let btn of buttons) {
                                // Basic check to ensure the button is actually rendered in the DOM
                                if (btn.offsetParent !== null) { 
                                    btn.click();
                                    count++;
                                }
                            }
                            return count;
                        }""")
                        
                        logging.info(f"Iteration {i+1}: Dispatched {clicks_dispatched} clicks via JS.")
                        
                        if clicks_dispatched == 0:
                            logging.info("No more expansion buttons found. Expansion complete.")
                            break
                            
                        # Wait a moment for the requested replies to render in the DOM
                        page.wait_for_timeout(4000)
                        
                        # Scroll down to ensure we trigger any lazy-loaded elements
                        page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                        page.wait_for_timeout(2000)
                        
                    except Exception as e:
                        logging.warning(f"Iteration {i+1} JS click failed: {str(e).splitlines()[0]}")
                        break

                logging.info("Proceeding to final extraction.")

                # Phase 3: Final scroll to ensure all loaded
                logging.info("Performing final scroll to load any remaining content...")
                page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                page.wait_for_timeout(5000)

                # Extract all loaded comments
                logging.info("Extracting all loaded comments...")
                author_locs = page.locator('#author-text')
                text_locs = page.locator('#content-text')
                extracted_count = text_locs.count()
                logging.info(f"Found {extracted_count} comment texts for extraction (including replies).")
                for i in range(extracted_count):
                    try:
                        author = author_locs.nth(i).text_content().strip()
                        text = text_locs.nth(i).text_content().strip()
                        c_id = generate_persistent_id(author, text)
                        if c_id in comments:
                            logging.debug(f"Duplicate comment detected at index {i}: {text[:50]}...")
                        else:
                            comments[c_id] = {
                                'a': author,
                                't': text,
                                'ts_posted': datetime.datetime.now().isoformat(),  # Approximate posted time, since scraping doesn't provide exact
                                'created_at': datetime.datetime.now().isoformat(),
                                'lastSeen': datetime.datetime.now().isoformat(),
                                'deleted': False,
                                'notFoundCounter': 0
                            }
                    except Exception as e:
                        logging.warning(f"Failed to extract comment {i}: {e}")
                
                logging.info(f"Extracted {len(comments)} unique comments after deduplication.")
                if ui_count == 0 and len(comments) > 0:
                    ui_count = len(comments)
                    
            return ui_count, comments, title
            
        except Exception as e:
            logging.error(f"Scrape failed for {v_id}: {e}")
            return None, None, None
        finally:
            browser.close()

def send_deletion_alert(author, text, v_id, ts, deleted_at, percentage, title):
    logging.info(f"Detected removed comment by '{author}': {text[:50]}... Sending deletion alert to Discord.")
    if not WEBHOOK:
        logging.warning("No Discord webhook configured. Skipping alert send.")
        return
    
    color = 0xFFEB3B if percentage <= 25 else 0xFFC107 if percentage <= 50 else 0xD32F2F
    
    payload = {
        "embeds": [{
            "title": "🚨 Deleted Comment Detected",
            "description": f"**Author:** `{author}`\n**Content:** {text[:800]}\n**Posted:** <t:{int(to_unix(ts))}:f>\n**Deleted:** <t:{int(to_unix(deleted_at))}:f>\n\n**{percentage:.1f}%** of tracked comments removed.",
            "color": color,
            "fields": [{"name": title, "value": f"[View Video](https://www.youtube.com/watch?v={v_id})", "inline": True}],
            "footer": {"text": f"Video ID: {v_id}"}
        }]
    }
    try:
        response = requests.post(WEBHOOK, json=payload)
        if response.status_code == 204:
            logging.info("Deletion alert sent successfully to Discord.")
            logging.error(f"Failed to send alert: Status {response.status_code} - {response.text}")
    except Exception as e:
        logging.error(f"Webhook failed: {e}")

# --- MAIN LOGIC ---
logging.info("Starting YouTube comment deletion monitor.")
if not os.path.exists(VIDEO_LIST):
    with open(VIDEO_LIST, "w") as f: json.dump(["Pt70d9k1MV8"], f)
    logging.info("Created videos.json with default ID. Add additional IDs as needed and restart.")
    sys.exit()

with open(VIDEO_LIST, "r") as f: 
    video_ids = json.load(f)
logging.info(f"Monitoring videos: {video_ids}")

history = {}
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding='utf-8') as f:
        history = json.load(f)
    logging.info("Loaded existing comment state.")
    migrate_timestamps(history)

for v_id in video_ids:
    logging.info(f"Processing video {v_id}.")
    
    # Always perform deep scrape to get current comments
    _, current_comments, title = get_yt_data(v_id, deep_scrape=True)
    if current_comments is None:
        logging.warning(f"Skipping video {v_id} due to scraping failure.")
        continue
    
    old_state = history.get(v_id, {"count": 0, "comments": {}})
    logging.info(f"Previous state for {v_id}: {old_state['count']} comments.")
    
    updated_comments = {}
    deletions = []
    
    # Process existing comments
    for c_id, comment_data in old_state["comments"].items():
        if c_id in current_comments:
            # Comment found, update lastSeen and reset counter
            updated_comments[c_id] = comment_data.copy()
            updated_comments[c_id]['lastSeen'] = datetime.datetime.now().isoformat()
            updated_comments[c_id]['notFoundCounter'] = 0
            logging.debug(f"Comment {c_id} still present, updated lastSeen.")
        else:
            # Comment not found, increment counter
            updated_comments[c_id] = comment_data.copy()
            updated_comments[c_id]['notFoundCounter'] = comment_data.get('notFoundCounter', 0) + 1
            logging.debug(f"Comment {c_id} not found, counter now {updated_comments[c_id]['notFoundCounter']}.")
            
            # Check if should mark as deleted
            if updated_comments[c_id]['notFoundCounter'] >= 3 and not comment_data.get('deleted', False):
                updated_comments[c_id]['deleted'] = True
                deletions.append(updated_comments[c_id])
    
    # Add new comments
    for c_id, comment_data in current_comments.items():
        if c_id not in updated_comments:
            updated_comments[c_id] = comment_data.copy()
            logging.debug(f"Added new comment {c_id}.")
    
    # Send alerts for newly detected deletions
    if deletions:
        total_tracked = len([c for c in updated_comments.values() if not c.get('deleted', False)])
        perc = (len(deletions) / max(total_tracked + len(deletions), 1)) * 100  # Approximate percentage
        logging.info(f"Detected {len(deletions)} new deletions for video {v_id}.")
        for d in deletions:
            logging.info(f"Marking as deleted: {d['a']} - {d['t'][:100]}...")
            send_deletion_alert(d['a'], d['t'], v_id, d.get('ts_posted', d.get('ts', time.time())), datetime.datetime.now().isoformat(), perc, title)
    
    # Update history
    history[v_id] = {
        "count": len(current_comments),
        "comments": updated_comments,
        "title": title,
        "last_checked": datetime.datetime.now().isoformat()
    }
    logging.info(f"Updated state for video {v_id} with {len(updated_comments)} comments.")

with open(STATE_FILE, "w", encoding='utf-8') as f:
    json.dump(history, f, indent=2, ensure_ascii=False)
logging.info("Comment state saved. Monitoring complete.")