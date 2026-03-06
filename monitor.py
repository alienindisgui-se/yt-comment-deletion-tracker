import json
import os
import requests
import time
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

# Fallback for .env file
if WEBHOOK is None and os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            if line.startswith('DISCORD_WEBHOOK='):
                WEBHOOK = line.split('=', 1)[1].strip()
                break

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def generate_persistent_id(author, text):
    raw_str = f"{author}|{text}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

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
                        if no_change >= 12:
                            logging.info(f"Loaded {current_thread} top-level threads.")
                            break
                    else:
                        no_change = 0
                        last_thread_count = current_thread
                    page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                    logging.info("Scrolled to bottom for thread loading.")
                    page.wait_for_timeout(5000)

                # # Phase 2: Iterative expansion of replies with individual dispatching
                # logging.info("Expanding all nested replies iteratively...")
                # expansion_iterations = 0
                # max_iterations = 2  # You can adjust back if needed
                # zero_click_count = 0
                # while expansion_iterations < max_iterations:
                #     expansion_iterations += 1
                #     locators = page.locator('ytd-button-renderer#more-replies').all()
                #     button_count = len(locators)
                #     logging.info(f"Iteration {expansion_iterations}: Found {button_count} expansion buttons.")
                #     if button_count == 0:
                #         logging.info("No more expansion buttons found. Expansion complete.")
                #         break
                    
                #     clicked = 0
                #     for i, loc in enumerate(locators):
                #         logging.info(f"Iteration {expansion_iterations}: Attempting to dispatch click on button {i+1}/{button_count}.")
                #         try:
                #             loc.scroll_into_view_if_needed(timeout=5000)
                #             logging.info(f"Iteration {expansion_iterations}: Button {i+1} scrolled into view.")
                #             loc.dispatch_event('click')
                #             clicked += 1
                #             page.wait_for_timeout(3000)  # Increased wait for replies to load
                #             logging.info(f"Iteration {expansion_iterations}: Successfully dispatched click on button {i+1}/{button_count}.")
                #         except Exception as e:
                #             logging.debug(f"Iteration {expansion_iterations}: Failed to dispatch click on button {i+1}/{button_count}: {e}")
                    
                #     logging.info(f"Iteration {expansion_iterations}: Dispatched {clicked} clicks in total.")
                #     if clicked == 0:
                #         zero_click_count += 1
                #         if zero_click_count >= 3:
                #             logging.warning("Three consecutive iterations with zero dispatches despite buttons found. Breaking to avoid loop.")
                #             break
                #     else:
                #         zero_click_count = 0
                    
                #     page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                #     logging.info("Scrolled to bottom after expansion attempt.")
                #     page.wait_for_timeout(5000)

                # if expansion_iterations >= max_iterations:
                #     logging.warning(f"Reached maximum expansion iterations ({max_iterations}). Proceeding to extraction.")

                # # Phase 3: Final scroll to ensure all loaded
                # logging.info("Performing final scroll to load any remaining content...")
                # page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                # page.wait_for_timeout(5000)

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
                            comments[c_id] = {'a': author, 't': text, 'ts': int(time.time())}
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
            "description": f"**Author:** `{author}`\n**Content:** {text[:800]}\n**Posted:** <t:{int(ts)}:f>\n**Deleted:** <t:{int(deleted_at)}:f>\n\n**{percentage:.1f}%** of tracked comments removed.",
            "color": color,
            "fields": [{"name": title, "value": f"[View Video](https://www.youtube.com/watch?v={v_id})", "inline": True}],
            "footer": {"text": f"Video ID: {v_id}"}
        }]
    }
    try:
        response = requests.post(WEBHOOK, json=payload)
        if response.status_code == 204:
            logging.info("Deletion alert sent successfully to Discord.")
        else:
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

for v_id in video_ids:
    ui_count, _, title = get_yt_data(v_id, deep_scrape=False)
    if ui_count is None: 
        logging.warning(f"Skipping video {v_id} due to loading failure.")
        continue

    old_state = history.get(v_id, {"count": 0, "comments": {}})
    logging.info(f"Previous state for {v_id}: {old_state['count']} comments.")
    
    if ui_count != old_state["count"] or not old_state["comments"]:
        if ui_count != old_state["count"]:
            logging.info(f"Comment count changed from {old_state['count']} to {ui_count}. Performing deep scrape.")
        else:
            logging.info("No previous comments stored. Performing initial deep scrape.")
        _, current_comments, _ = get_yt_data(v_id, deep_scrape=True)
        
        if current_comments:
            if old_state["comments"]:
                deletions = [data for c_id, data in old_state["comments"].items() if c_id not in current_comments]
                if deletions:
                    logging.info(f"Detected {len(deletions)} deleted comments for video {v_id}.")
                    perc = (len(deletions) / len(old_state["comments"]) * 100)
                    for d in deletions:
                        send_deletion_alert(d['a'], d['t'], v_id, d['ts'], time.time(), perc, title)

            history[v_id] = {
                "count": len(current_comments) if current_comments else ui_count,
                "comments": current_comments,
                "title": title,
                "last_checked": time.time()
            }
            logging.info(f"Updated state for video {v_id} with {len(current_comments)} comments.")

with open(STATE_FILE, "w", encoding='utf-8') as f:
    json.dump(history, f, indent=2, ensure_ascii=False)
logging.info("Comment state saved. Monitoring complete.")