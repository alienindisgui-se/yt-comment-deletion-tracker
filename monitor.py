import json
import os
import requests
import time
import random
import sys
import logging
import hashlib
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging and encoding
sys.stdout.reconfigure(encoding='utf-8')

# Create run log filename
run_number = 1
while os.path.exists(f"run{run_number}.log"):
    run_number += 1
log_filename = f"run{run_number}.log"

# Setup both console and file logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

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

def fetch_latest_videos(channels):
    """Fetch latest videos from specified YouTube channels"""
    latest_videos = []
    user_agent = random.choice(USER_AGENTS)
    logging.info(f"Selected user agent for fetching: {user_agent}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'}
        )
        page = context.new_page()
        
        for channel in channels:
            try:
                url = f"https://www.youtube.com/@{channel}/videos"
                page.goto(url, timeout=60000)
                page.wait_for_load_state('networkidle')
                
                # Handle YouTube consent page
                if "Before you continue to YouTube" in page.title():
                    try:
                        accept_button = page.locator('button').filter(has_text="Accept all").first
                        if accept_button.count() > 0:
                            accept_button.click()
                            page.wait_for_timeout(2000)
                            page.wait_for_load_state('networkidle')
                        else:
                            logging.warning("Accept button not found on consent page.")
                    except Exception as e:
                        logging.error(f"Failed to accept consent: {e}")
                
                # Scroll to load videos
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(3000)
                
                # Find the first video link
                rich_count = page.locator('ytd-rich-item-renderer').count()
                link_count = page.locator('ytd-rich-item-renderer a[href*="/watch?v="]').count()
                video_locator = page.locator('ytd-rich-item-renderer a[href*="/watch?v="]').first
                if video_locator.count() > 0:
                    href = video_locator.get_attribute('href')
                    if href and 'v=' in href:
                        v_id = href.split('v=')[1].split('&')[0]
                        latest_videos.append(v_id)
                    else:
                        logging.warning(f"Invalid href for channel {channel}: {href}")
                else:
                    logging.warning("No video links found in rich items, trying grid media fallback")
                    grid_link_count = page.locator('ytd-rich-grid-media a[href*="/watch?v="]').count()
                    logging.info(f"Found {grid_link_count} video links in grid media")
                    video_locator = page.locator('ytd-rich-grid-media a[href*="/watch?v="]').first
                    if video_locator.count() > 0:
                        href = video_locator.get_attribute('href')
                        logging.info(f"Fallback video href: {href}")
                        if href and 'v=' in href:
                            v_id = href.split('v=')[1].split('&')[0]
                            latest_videos.append(v_id)
                            logging.info(f"Fetched latest video {v_id} for channel {channel} using fallback")
                        else:
                            logging.warning(f"Invalid fallback href for channel {channel}: {href}")
                    else:
                        logging.warning(f"No videos found on page for channel {channel}")
            except Exception as e:
                logging.error(f"Failed to fetch latest video for {channel}: {e}")
        
        page.close()
        browser.close()
    
    return latest_videos

def parse_youtube_timestamp(timestamp_text, current_time):
    """Parse YouTube relative timestamps like '3 hours ago' into actual datetime"""
    if not timestamp_text or timestamp_text == "NO_TIMESTAMP_FOUND":
        return current_time
    
    timestamp_text = timestamp_text.strip().lower()
    
    # Parse the number and unit
    import re
    match = re.match(r'(\d+)\s*(second|minute|hour|day)s?\s*ago', timestamp_text)
    if not match:
        return current_time  # Return current time if parsing fails
    
    number = int(match.group(1))
    unit = match.group(2)
    
    # Calculate the time difference
    from datetime import timedelta
    if unit == 'second':
        time_diff = timedelta(seconds=number)
    elif unit == 'minute':
        time_diff = timedelta(minutes=number)
    elif unit == 'hour':
        time_diff = timedelta(hours=number)
    elif unit == 'day':
        time_diff = timedelta(days=number)
    else:
        return current_time
    
    # Subtract the time difference from current time
    actual_time = current_time - time_diff
    return actual_time

def get_yt_data(v_id, deep_scrape=False):
    user_agent = random.choice(USER_AGENTS)
    
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
            page.goto(f"https://www.youtube.com/watch?v={v_id}", timeout=60000)
            page.wait_for_load_state('networkidle')
            
            # Scroll to trigger the comment section
            page.evaluate("window.scrollBy(0, 800)")
            
            try:
                page.wait_for_selector('ytd-comments#comments', state='attached', timeout=15000)
                page.wait_for_timeout(3000)
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
                except Exception as e:
                    logging.warning(f"Failed to sort comments: {e}. Proceeding with default sort.")

            # Get Title
            title_elem = page.locator('h1.ytd-watch-metadata yt-formatted-string')
            title = title_elem.text_content().strip() if title_elem.count() > 0 else 'Unknown'
            
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

            comments = []
            if deep_scrape:
                
                # Phase 1: Load all top-level threads by scrolling
                last_thread_count = 0
                no_change = 0
                while True:
                    thread_nodes = page.locator('ytd-comment-thread-renderer')
                    current_thread = thread_nodes.count()
                    if current_thread == last_thread_count:
                        no_change += 1
                        if no_change >= 2:
                            break
                    else:
                        no_change = 0
                        last_thread_count = current_thread
                    page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                    page.wait_for_timeout(3000)


                # Phase 3: Final scroll to ensure all loaded
                page.evaluate("document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight")
                page.wait_for_timeout(3000)

                # Extract all loaded comments
                author_locs = page.locator('#author-text')
                text_locs = page.locator('#content-text')
                time_locs = page.locator('#published-time-text')
                extracted_count = text_locs.count()
                
                for i in range(extracted_count):
                    try:
                        author = author_locs.nth(i).text_content().strip()
                        text = text_locs.nth(i).text_content().strip()
                        time_text = time_locs.nth(i).text_content().strip() if time_locs.count() > i else "NO_TIMESTAMP_FOUND"
                        
                        # Check if comment already exists in array
                        existing_comment = None
                        for comment in comments:
                            if comment['a'] == author and comment['t'] == text:
                                existing_comment = comment
                                break
                        
                        if existing_comment:
                            logging.debug(f"Duplicate comment detected at index {i}: {text[:50]}...")
                        else:
                            # Parse the YouTube timestamp to get actual posting time
                            current_time = datetime.now()
                            first_seen_time = parse_youtube_timestamp(time_text, current_time)
                            
                            comments.append({
                                'a': author,
                                't': text,
                                'firstSeen': first_seen_time.isoformat(),
                                'lastSeen': current_time.isoformat(),
                                'deleted': False,
                                'notFoundCounter': 0
                            })
                    except Exception as e:
                        logging.warning(f"Failed to extract comment {i}: {e}")
                
                # # Save timestamps to separate JSON file for debugging
                # timestamps_data = []
                # for i in range(extracted_count):
                #     try:
                #         author = author_locs.nth(i).text_content().strip()
                #         text = text_locs.nth(i).text_content().strip()
                #         time_text = time_locs.nth(i).text_content().strip() if time_locs.count() > i else "NO_TIMESTAMP_FOUND"
                        
                #         timestamps_data.append({
                #             'index': i,
                #             'author': author,
                #             'text_preview': text[:100] + "..." if len(text) > 100 else text,
                #             'timestamp': time_text
                #         })
                #     except Exception as e:
                #         timestamps_data.append({
                #             'index': i,
                #             'author': "EXTRACTION_ERROR",
                #             'text_preview': "",
                #             'timestamp': f"ERROR: {e}"
                #         })
                
                # # Save timestamps data
                # timestamps_filename = f"timestamps_{v_id}.json"
                # with open(timestamps_filename, "w", encoding='utf-8') as f:
                #     json.dump(timestamps_data, f, indent=2, ensure_ascii=False)
                
                # if ui_count == 0 and len(comments) > 0:
                #     ui_count = len(comments)
                    
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
    
    # Convert ISO-8601 timestamps to Unix timestamps for Discord
    def iso_to_unix(iso_timestamp):
        if isinstance(iso_timestamp, str):
            try:
                dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
                return int(dt.timestamp())
            except (ValueError, AttributeError):
                return int(time.time())
        return int(iso_timestamp) if isinstance(iso_timestamp, (int, float)) else int(time.time())
    
    color = 0xFFEB3B if percentage <= 25 else 0xFFC107 if percentage <= 50 else 0xD32F2F
    
    payload = {
        "embeds": [{
            "title": "🚨 Deleted Comment Detected",
            "description": f"**Author:** `{author}`\n**Content:** {text[:800]}\n**Posted:** <t:{iso_to_unix(ts)}:f>\n**Deleted:** <t:{iso_to_unix(deleted_at)}:f>\n\n**{percentage:.1f}%** of tracked comments removed.",
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
# Start total runtime timer
script_start_time = time.time()

# Fetch latest videos from channels before processing
channels_env = os.getenv('CHANNELS_LIST', '')
logging.info(f"CHANNELS_LIST environment variable: '{channels_env}'")
channels = channels_env.split(',') if channels_env else []

if channels:
    fetched_videos = fetch_latest_videos(channels)
    if fetched_videos:
        # Load existing videos
        existing_videos = []
        if os.path.exists(VIDEO_LIST):
            try:
                with open(VIDEO_LIST, "r") as f:
                    existing_videos = json.load(f)
                logging.info(f"Loaded {len(existing_videos)} existing videos from {VIDEO_LIST}")
            except Exception as e:
                logging.warning(f"Failed to load existing videos: {e}")
        
        # Merge new videos with existing ones
        all_videos = list(dict.fromkeys(existing_videos + fetched_videos))  # Remove duplicates while preserving order
        
        # Save updated video list
        with open(VIDEO_LIST, "w", encoding='utf-8') as f:
            json.dump(all_videos, f, indent=2)
        logging.info(f"Updated videos list with {len(fetched_videos)} new videos. Total: {len(all_videos)}")
    else:
        logging.warning("No videos fetched from channels.")
else:
    logging.info("No channels configured, using existing video list.")

# Load video IDs for monitoring
if not os.path.exists(VIDEO_LIST):
    with open(VIDEO_LIST, "w") as f:
        json.dump(["Pt70d9k1MV8"], f)
    logging.info("Created videos.json with default ID. Add additional IDs as needed and restart.")
    sys.exit()

video_ids = []
with open(VIDEO_LIST, "r") as f:
    video_ids = json.load(f)
logging.info(f"Monitoring {len(video_ids)} videos")

history = {}
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r", encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                history = json.loads(content)
                logging.info("Loaded existing comment state.")
            else:
                logging.info("Comment state file is empty, starting fresh.")
                history = {}
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logging.warning(f"Failed to load comment state file: {e}. Starting fresh.")
        history = {}

# Initialize summary statistics
total_new_comments = 0
total_deleted_comments = 0
total_videos_processed = 0
videos_with_changes = 0
videos_with_no_changes = 0
videos_archived = 0
archived_video_ids = []

for v_id in video_ids:
    start_time = time.time()
    logging.info(f"Processing video {v_id}.")
    
    # Check if video should be archived (older than 4 days)
    old_state = history.get(v_id, {"visible_count": 0, "tracked_count": 0, "comments": []})
    if old_state.get("comments"):
        # Find the oldest comment's firstSeen date
        oldest_first_seen = None
        for comment in old_state["comments"]:
            first_seen = comment.get("firstSeen")
            if first_seen:
                if oldest_first_seen is None or first_seen < oldest_first_seen:
                    oldest_first_seen = first_seen
        
        if oldest_first_seen:
            try:
                oldest_date = datetime.fromisoformat(oldest_first_seen.replace('Z', '+00:00'))
                days_old = (datetime.now() - oldest_date).days
                if days_old > 4:
                    logging.info(f"Video {v_id} is {days_old} days old, marking as archived and skipping.")
                    # Mark as archived in state
                    old_state["archived"] = True
                    old_state["archived_date"] = datetime.now().isoformat()
                    history[v_id] = old_state
                    videos_archived += 1
                    archived_video_ids.append(v_id)
                    continue
            except ValueError:
                logging.warning(f"Invalid date format for video {v_id}: {oldest_first_seen}")
    
    # Always perform deep scrape to get current comments
    ui_count, current_comments, title = get_yt_data(v_id, deep_scrape=True)
    if current_comments is None:
        logging.warning(f"Skipping video {v_id} due to scraping failure.")
        continue
    
    old_state = history.get(v_id, {"visible_count": 0, "tracked_count": 0, "comments": []})
    
    updated_comments = []
    deletions = []
    
    # Process existing comments
    for old_comment in old_state["comments"]:
        # Find matching comment in current data
        matching_comment = None
        for current_comment in current_comments:
            if current_comment['a'] == old_comment['a'] and current_comment['t'] == old_comment['t']:
                matching_comment = current_comment
                break
        
        if matching_comment:
            # Comment found, update lastSeen and reset counter
            updated_comment = old_comment.copy()
            updated_comment['lastSeen'] = datetime.now().isoformat()
            updated_comment['notFoundCounter'] = 0
            updated_comments.append(updated_comment)
            logging.debug(f"Comment by {old_comment['a']} still present, updated lastSeen.")
        else:
            # Comment not found, increment counter
            updated_comment = old_comment.copy()
            updated_comment['notFoundCounter'] = old_comment.get('notFoundCounter', 0) + 1
            updated_comments.append(updated_comment)
            logging.debug(f"Comment by {old_comment['a']} not found, counter now {updated_comment['notFoundCounter']}.")
            
            # Check if should mark as deleted
            if updated_comment['notFoundCounter'] >= 3 and not old_comment.get('deleted', False):
                updated_comment['deleted'] = True
                deletions.append(updated_comment)
    
    # Add new comments
    video_new_comments = 0
    for current_comment in current_comments:
        # Check if comment already exists in updated list
        exists = False
        for updated_comment in updated_comments:
            if updated_comment['a'] == current_comment['a'] and updated_comment['t'] == current_comment['t']:
                exists = True
                break
        
        if not exists:
            updated_comments.append(current_comment.copy())
            video_new_comments += 1
            logging.debug(f"Added new comment by {current_comment['a']}.")
    
    # Send alerts for newly detected deletions
    if deletions:
        total_tracked = len([c for c in updated_comments if not c.get('deleted', False)])
        perc = (len(deletions) / max(total_tracked + len(deletions), 1)) * 100  # Approximate percentage
        logging.info(f"Detected {len(deletions)} new deletions for video {v_id}.")
        for d in deletions:
            logging.info(f"Marking as deleted: {d['a']} - {d['t'][:100]}...")
            send_deletion_alert(d['a'], d['t'], v_id, d.get('firstSeen', d.get('ts_posted', d.get('ts', datetime.now().isoformat()))), datetime.now().isoformat(), perc, title)
    
    # Track statistics for this video
    video_deleted_count = len(deletions)
    video_changes = video_new_comments + video_deleted_count
    
    if video_changes > 0:
        videos_with_changes += 1
        logging.info(f"Video '{title}[{v_id}]' changes: +{video_new_comments} new, -{video_deleted_count} deleted")
    else:
        videos_with_no_changes += 1
        logging.info(f"Video '{title}[{v_id}]' no changes detected")
    
    # Update global statistics
    total_new_comments += video_new_comments
    total_deleted_comments += video_deleted_count
    total_videos_processed += 1
    
    # Update history
    history[v_id] = {
        "visible_count": ui_count,
        "tracked_count": len(updated_comments),
        "comments": updated_comments,
        "title": title,
        "last_checked": datetime.now().isoformat()
    }
    
    # Log processing time
    end_time = time.time()
    processing_time = end_time - start_time
    logging.info(f"Completed processing video '{title}[{v_id}]' in {processing_time:.2f} seconds.")

# Print summary statistics before final save
logging.info("=" * 60)
logging.info("COMMENT MONITORING SUMMARY")
logging.info("=" * 60)
logging.info(f"Total videos processed: {total_videos_processed}")
logging.info(f"Videos with changes: {videos_with_changes}")
logging.info(f"Videos with no changes: {videos_with_no_changes}")
if videos_archived > 0:
    logging.info(f"Videos archived: {videos_archived}")
logging.info(f"Total new comments: {total_new_comments}")
logging.info(f"Total deleted comments: {total_deleted_comments}")
logging.info(f"Net comment change: {total_new_comments - total_deleted_comments}")
if total_deleted_comments > 0:
    logging.info(f"  {total_deleted_comments} comments were marked as deleted across all videos")
if total_new_comments > 0:
    logging.info(f" {total_new_comments} new comments were added across all videos")
if videos_with_no_changes == total_videos_processed:
    logging.info("  No changes detected in any monitored videos")
logging.info("=" * 60)

# Calculate total runtime
script_end_time = time.time()
total_runtime = script_end_time - script_start_time
hours = int(total_runtime // 3600)
minutes = int((total_runtime % 3600) // 60)
seconds = int(total_runtime % 60)

if hours > 0:
    runtime_str = f"{hours}h {minutes}m {seconds}s"
elif minutes > 0:
    runtime_str = f"{minutes}m {seconds}s"
else:
    runtime_str = f"{seconds}s"

logging.info(f"Total runtime: {runtime_str}")

# Remove archived videos from videos.json list
if archived_video_ids:
    try:
        # Load current video list
        with open(VIDEO_LIST, "r") as f:
            current_video_ids = json.load(f)
        
        # Remove archived videos from the list
        updated_video_ids = [vid for vid in current_video_ids if vid not in archived_video_ids]
        
        # Save updated list
        with open(VIDEO_LIST, "w", encoding='utf-8') as f:
            json.dump(updated_video_ids, f, indent=2)
        
        logging.info(f"Removed {len(archived_video_ids)} archived videos from {VIDEO_LIST}")
        for archived_id in archived_video_ids:
            logging.info(f"  - Removed archived video: {archived_id}")
    except Exception as e:
        logging.warning(f"Failed to update {VIDEO_LIST}: {e}")

with open(STATE_FILE, "w", encoding='utf-8') as f:
    json.dump(history, f, indent=2, ensure_ascii=False)
logging.info("Comment state saved. Monitoring complete.")