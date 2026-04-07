import json
import os
import requests
import time
import random
import sys
import logging
import hashlib
import re
import functools
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Timing decorator for debugging
def timer(func):
    """Decorator to time function execution"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logging.info(f"⏱️  {func.__name__}() executed in {execution_time:.2f}s")
        return result
    return wrapper

# Helper functions for date parsing
# @timer
def _parse_date_from_text(text):
    """Parse date from various text formats"""
    try:
        # Handle "X days ago" format
        days_match = re.search(r'(\d+)\s+days?\s+ago', text, re.IGNORECASE)
        if days_match:
            days_ago = int(days_match.group(1))
            return datetime.now() - timedelta(days=days_ago)
        
        # Handle "X hours ago" format
        hours_match = re.search(r'(\d+)\s+hours?\s+ago', text, re.IGNORECASE)
        if hours_match:
            hours_ago = int(hours_match.group(1))
            if hours_ago < 24:  # Only count if less than a day old
                return datetime.now() - timedelta(hours=hours_ago)
        
        # Handle "Premiered on DATE" or "Published on DATE" format
        date_match = re.search(r'(?:Premiered|Published|Streamed)\s+on\s+(.+)', text, re.IGNORECASE)
        if date_match:
            date_str = date_match.group(1)
            # Try different date formats
            for fmt in ['%b %d, %Y', '%d %b %Y', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
        
        # Handle direct date patterns
        date_patterns = [
            r'(\w{3}\s+\d{1,2},\s+\d{4})',  # "Apr 6, 2026"
            r'(\d{1,2}\s+\w{3}\s+\d{4})',  # "6 Apr 2026"
            r'(\d{4}-\d{2}-\d{2})',        # "2026-04-06"
            r'(\d{2}/\d{2}/\d{4})',        # "04/06/2026"
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                date_str = match.group(1)
                for fmt in ['%b %d, %Y', '%d %b %Y', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
        
    except Exception as e:
        logging.debug(f"Date parsing failed for '{text}': {e}")
    
    return None

@timer
def _search_dates_in_page_content(page_content):
    """Search for dates in page HTML content"""
    try:
        # Look for date patterns in JSON-LD structured data
        json_ld_match = re.search(r'"datePublished":\s*"([^"]+)"', page_content)
        if json_ld_match:
            date_str = json_ld_match.group(1)
            try:
                # Handle ISO format dates
                if 'T' in date_str:
                    date_str = date_str.split('T')[0]
                return datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                pass
        
        # Look for upload date in page content
        upload_match = re.search(r'"uploadDate":\s*"([^"]+)"', page_content)
        if upload_match:
            date_str = upload_match.group(1)
            try:
                if 'T' in date_str:
                    date_str = date_str.split('T')[0]
                return datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                pass
        
        # Look for any date-like patterns in the content
        date_patterns = [
            r'(\d{4}-\d{2}-\d{2})',
            r'(\d{2}/\d{2}/\d{4})',
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, page_content)
            if matches:
                # Take the first match that looks reasonable
                for date_str in matches[:3]:  # Check first 3 matches
                    try:
                        if '-' in date_str:
                            return datetime.strptime(date_str, '%Y-%m-%d')
                        elif '/' in date_str:
                            return datetime.strptime(date_str, '%m/%d/%Y')
                    except ValueError:
                        continue
    
    except Exception as e:
        logging.debug(f"Page content date search failed: {e}")
    
    return None

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
ACCEPT_LANGUAGE_HEADER = 'en-US,en;q=0.9'

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def is_members_only_video(page):
    """Check if video is members only"""
    try:
        # Very specific "members only" indicators - be extremely conservative
        members_indicators = [
            # Look for explicit "Members only" badges or text in video context
            'span:has-text("Members only")',
            'yt-formatted-string:has-text("Members only")',
            # Specific YouTube membership elements
            'ytd-members-only-renderer',
            # Only check for aria-label that explicitly mentions "members only"
            '[aria-label*="Members only"]'
        ]
        
        for indicator in members_indicators:
            try:
                elements = page.locator(indicator)
                if elements.count() > 0:
                    # Additional verification - check if the element is actually visible
                    if elements.first.is_visible():
                        logging.info(f"DEBUG: Found members-only indicator: {indicator}")
                        return True
            except (Exception, TimeoutError):
                continue
        
        # Check page content for "members only" text - be very specific
        page_content = page.content()
        # Look for "members only" in specific contexts, not just anywhere
        if re.search(r'members\s+only.*video|video.*members\s+only', page_content, re.IGNORECASE):
            logging.info("DEBUG: Found members-only text in page content")
            return True
            
    except Exception as e:
        logging.debug(f"Error checking members only: {e}")
        return False
    
    return False

def generate_persistent_id(author, text):
    raw_str = f"{author}|{text}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

@timer
def fetch_latest_videos(channels):
    """Fetch latest videos from specified YouTube channels"""
    latest_videos = []
    user_agent = random.choice(USER_AGENTS)
    # logging.info(f"Selected user agent for fetching: {user_agent}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            extra_http_headers={'Accept-Language': ACCEPT_LANGUAGE_HEADER}
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

# @timer
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

# @timer
def get_publish_date_only(v_id):
    """Quick function to get just the publish date without scraping comments"""
    # First check if we already have the publish date cached
    if v_id in history and 'published' in history[v_id]:
        try:
            cached_date = datetime.fromisoformat(history[v_id]['published'])
            # logging.info(f"📋 Using cached publish date for {v_id}: {cached_date}")
            return cached_date
        except (ValueError, TypeError) as e:
            logging.warning(f"Invalid cached date format for {v_id}: {e}")
    
    # If not cached, scrape it
    logging.info(f"🌐 Scraping publish date for {v_id} (not cached)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            extra_http_headers={'Accept-Language': ACCEPT_LANGUAGE_HEADER}
        )
        page = context.new_page()
        
        try:
            page.goto(f"https://www.youtube.com/watch?v={v_id}", timeout=60000)
            page.wait_for_load_state('networkidle')
            
            # Get publish date using the same logic as get_yt_data
            publish_date = None
            try:
                date_selectors = [
                    "#info-text",
                    "#description-inner yt-formatted-string.ytd-video-secondary-info-renderer",
                    "#info-strings yt-formatted-string.ytd-video-secondary-info-renderer",
                    ".ytd-video-secondary-info-renderer yt-formatted-string",
                    ".ytd-video-primary-info-renderer .ytd-simple-timestamp-renderer",
                    "ytd-video-view-model-renderer .ytd-simple-timestamp-renderer",
                    "ytd-metadata-row-renderer .ytd-simple-timestamp-renderer",
                    ".ytd-simple-timestamp-renderer",
                    "#meta-contents ytd-video-secondary-info-renderer yt-formatted-string",
                    "span.ytd-video-secondary-info-renderer",
                    "span.ytd-watch-metadata[aria-label*=\"Published\"], span.ytd-watch-metadata[aria-label*=\"Premiered\"], span.ytd-watch-metadata[aria-label*=\"Streamed\"]"
                ]

                for i, selector in enumerate(date_selectors):
                    try:
                        date_elem = page.locator(selector)
                        if date_elem.count() > 0:
                            date_text = date_elem.first.text_content().strip() if selector != date_selectors[-1] else date_elem.first.get_attribute('aria-label')
                            if date_text:
                                logging.info(f"🔍 Trying selector {i+1}/{len(date_selectors)}: '{selector}' found text: '{date_text[:50]}...'")
                                parsed_date = _parse_date_from_text(date_text)
                                if parsed_date:
                                    publish_date = parsed_date
                                    # logging.info(f"✅ Successfully parsed date from selector [{selector}] {i+1}: {parsed_date}")
                                    # Cache the publish date for future use
                                    if v_id not in history:
                                        history[v_id] = {"visible_count": 0, "tracked_count": 0, "comments": []}
                                    history[v_id]['published'] = parsed_date.isoformat()
                                    logging.info(f"💾 Cached publish date for {v_id}: {parsed_date}")
                                    # Save cache immediately to prevent loss
                                    try:
                                        with open(STATE_FILE, "w", encoding='utf-8') as f:
                                            json.dump(history, f, indent=2, ensure_ascii=False)
                                        logging.info(f"💾 Saved cache to {STATE_FILE}")
                                    except Exception as e:
                                        logging.warning(f"Failed to save cache: {e}")
                                    break
                    except Exception as e:
                        logging.warning(f"❌ Selector {i+1}/{len(date_selectors)} failed: {e}")
                        continue

                # If still not found, try searching page content
                if not publish_date:
                    page_content = page.content()
                    parsed_date = _search_dates_in_page_content(page_content)
                    if parsed_date:
                        publish_date = parsed_date
                        # Cache the publish date
                        if v_id not in history:
                            history[v_id] = {"visible_count": 0, "tracked_count": 0, "comments": []}
                        history[v_id]['published'] = parsed_date.isoformat()
                        logging.info(f"💾 Cached publish date from page content for {v_id}: {parsed_date}")
                        # Save cache immediately
                        try:
                            with open(STATE_FILE, "w", encoding='utf-8') as f:
                                json.dump(history, f, indent=2, ensure_ascii=False)
                            logging.info(f"💾 Saved cache to {STATE_FILE}")
                        except Exception as e:
                            logging.warning(f"Failed to save cache: {e}")

            except Exception:
                pass
            
            return publish_date
            
        except Exception:
            return None
        finally:
            browser.close()

# @timer
def get_yt_data(v_id, deep_scrape=False):
    user_agent = random.choice(USER_AGENTS)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            extra_http_headers={'Accept-Language': ACCEPT_LANGUAGE_HEADER}
        )
        page = context.new_page()
        
        try:
            page.goto(f"https://www.youtube.com/watch?v={v_id}", timeout=60000)
            page.wait_for_load_state('networkidle')
            
            # Get Title and publish date early for logging and archiving
            title_elem = page.locator('h1.ytd-watch-metadata yt-formatted-string')
            title = title_elem.text_content().strip() if title_elem.count() > 0 else 'Unknown'
            
            # Get publish date for archiving check
            publish_date = None
            try:
                # Try multiple selectors for better date extraction
                date_selectors = [
                    "#info-text", #WORKING
                    "#description-inner yt-formatted-string.ytd-video-secondary-info-renderer",
                    "#info-strings yt-formatted-string.ytd-video-secondary-info-renderer",
                    ".ytd-video-secondary-info-renderer yt-formatted-string",
                    ".ytd-video-primary-info-renderer .ytd-simple-timestamp-renderer",
                    "ytd-video-view-model-renderer .ytd-simple-timestamp-renderer",
                    "ytd-metadata-row-renderer .ytd-simple-timestamp-renderer",
                    ".ytd-simple-timestamp-renderer",
                    "#meta-contents ytd-video-secondary-info-renderer yt-formatted-string",
                    "span.ytd-video-secondary-info-renderer",
                    "span.ytd-watch-metadata[aria-label*=\"Published\"], span.ytd-watch-metadata[aria-label*=\"Premiered\"], span.ytd-watch-metadata[aria-label*=\"Streamed\"]"
                ]

                for i, selector in enumerate(date_selectors):
                    try:
                        date_elem = page.locator(selector)
                        if date_elem.count() > 0:
                            date_text = date_elem.first.text_content().strip() if selector != date_selectors[-1] else date_elem.first.get_attribute('aria-label')
                            if date_text:
                                # logging.info(f"🔍 Deep scrape trying selector {i+1}/{len(date_selectors)}: '{selector}' found text: '{date_text[:50]}...'")
                                parsed_date = _parse_date_from_text(date_text)
                                if parsed_date:
                                    publish_date = parsed_date
                                    # logging.info(f"✅ Successfully parsed date from selector [{selector}] {i+1}: {parsed_date}")
                                    break
                    except Exception as e:
                        logging.warning(f"❌ Deep scrape selector {i+1}/{len(date_selectors)} failed: {e}")
                        continue

                # If still not found, try searching page content
                if not publish_date:
                    logging.info("Searching page content for date patterns...")
                    page_content = page.content()
                    parsed_date = _search_dates_in_page_content(page_content)
                    if parsed_date:
                        publish_date = parsed_date
                        logging.info(f"Found date in page content: {parsed_date}")

            except Exception as e:
                logging.warning(f"Failed to get publish date: {e}")
            
            from datetime import datetime
            # Only log processing info for deep scrape to avoid duplicate logs
            if deep_scrape:
                logging.info(f"Processing video '{v_id}' with title '{title}'")
            
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
                            from datetime import datetime
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
                    
            return ui_count, comments, title, publish_date
            
        except Exception as e:
            logging.error(f"Scrape failed for {v_id}: {e}")
            return None, None, None, None
        finally:
            browser.close()

@timer
def get_gradient_color(percentage):
    """Generate a color gradient from red to green based on like ratio (0-100)"""
    # Ensure like_ratio is within 0-100 range
    ratio = max(0, min(100, percentage))

    # Red (255, 0, 0) at 0% to Green (0, 255, 0) at 100%
    red = int(255 * (1 - ratio / 100))
    green = int(255 * (ratio / 100))
    blue = 0

    # Convert to hex color string
    hex_color = f"{red:02x}{green:02x}{blue:02x}"
    return int(hex_color, 16)

@timer
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
    
    color = get_gradient_color(percentage)
    
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

# Start total runtime timer
script_start_time = time.time()

# Fetch latest videos from channels before processing
channels_env = os.getenv('CHANNELS_LIST')
channels = channels_env.split(',') if channels_env and channels_env != 'None' else []

if channels:
    fetched_videos = fetch_latest_videos(channels)
    if fetched_videos:
        # Load existing videos
        existing_data = {"active": [], "archived": []}
        if os.path.exists(VIDEO_LIST):
            try:
                with open(VIDEO_LIST, "r") as f:
                    data = json.load(f)
                    existing_data = {"active": data.get("active", []), "archived": data.get("archived", [])}
            except Exception as e:
                logging.warning(f"Failed to load existing videos: {e}")
        
        # Merge new videos with existing active ones (skip if already in archived)
        all_active = list(dict.fromkeys(existing_data["active"] + fetched_videos))
        # Remove any videos that are in archived from active
        all_active = [vid for vid in all_active if vid not in existing_data["archived"]]
        
        # Save updated video list with both active and archived
        updated_data = {
            "active": all_active,
            "archived": existing_data["archived"]
        }
        
        with open(VIDEO_LIST, "w", encoding='utf-8') as f:
            json.dump(updated_data, f, indent=2)
        # logging.info(f"Updated videos list with {len(fetched_videos)} new videos. Total active: {len(all_active)}, archived: {len(existing_data['archived'])}")
    else:
        logging.warning("No videos fetched from channels.")
else:
    logging.info("No channels configured, using existing video list.")

# Load video IDs for monitoring
if not os.path.exists(VIDEO_LIST):
    # Create new format with active and archived arrays
    default_data = {"active": [], "archived": []}
    with open(VIDEO_LIST, "w") as f:
        json.dump(default_data, f)
    logging.info("Created videos.json with empty active list. Add video IDs to the 'active' array and restart.")
    sys.exit()

# Load video data and extract only active videos for monitoring
with open(VIDEO_LIST, "r") as f:
    data = json.load(f)
    video_ids = data.get("active", [])

    archived_count = len(data.get("archived", []))

history = {}
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r", encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                history = json.loads(content)
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
    
    # Quick check for archiving based on publish date first (fast operation)
    publish_date = get_publish_date_only(v_id)
    if not publish_date:
        logging.warning(f"Could not get publish date for {v_id}, skipping.")
        continue
        
    # Check if video is members only and skip if so
    members_only = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            extra_http_headers={'Accept-Language': ACCEPT_LANGUAGE_HEADER}
        )
        page = context.new_page()
        
        try:
            page.goto(f"https://www.youtube.com/watch?v={v_id}", timeout=60000)
            page.wait_for_load_state('networkidle', timeout=10000)
            
            if is_members_only_video(page):
                logging.info(f"Video [{v_id}] is members only, skipping.")
                members_only = True
            else:
                logging.debug(f"Video [{v_id}] is not members only, continuing processing.")
        except TimeoutError as e:
            logging.warning(f"Timeout checking members only for {v_id}, assuming video is public and continuing: {e}")
        except Exception as e:
            logging.warning(f"Failed to check members only for {v_id}, assuming video is public and continuing: {e}")
        finally:
            browser.close()
    
    if members_only:
        continue
    
    if publish_date:
        days_old = (datetime.now() - publish_date).days
        if days_old > 4:
            logging.info(f"Video [{v_id}] is {days_old} days old (based on publish date), marking as archived and skipping.")
            # Mark as archived in state
            old_state = history.get(v_id, {"visible_count": 0, "tracked_count": 0, "comments": []})
            old_state["archived"] = True
            old_state["archived_date"] = datetime.now().isoformat()
            history[v_id] = old_state
            videos_archived += 1
            archived_video_ids.append(v_id)
            continue
    
    # Check if video should be archived based on existing comment history
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
                    logging.info(f"Video [{v_id}] is {days_old} days old (based on oldest comment), marking as archived and skipping.")
                    # Mark as archived in state
                    old_state["archived"] = True
                    old_state["archived_date"] = datetime.now().isoformat()
                    history[v_id] = old_state
                    videos_archived += 1
                    archived_video_ids.append(v_id)
                    continue
            except ValueError:
                logging.warning(f"Invalid date format for video {v_id}: {oldest_first_seen}")
    
    # Only perform expensive deep scrape if video is not being archived
    ui_count, current_comments, title, publish_date = get_yt_data(v_id, deep_scrape=True)
    if current_comments is None:
        logging.warning(f"Skipping video {v_id} due to scraping failure.")
        continue
    
    # Cache publish date if we got it from deep scrape and it's not already cached
    if publish_date and v_id in history and 'published' not in history[v_id]:
        history[v_id]['published'] = publish_date.isoformat()
        logging.info(f"💾 Cached publish date from deep scrape for {v_id}: {publish_date}")
    
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
        perc = (len(deletions) / max(total_tracked + len(deletions), 1)) * 100
        logging.info(f"Detected {len(deletions)} new deletions for video [{v_id}].")
        for d in deletions:
            send_deletion_alert(d['a'], d['t'], v_id, d.get('firstSeen', d.get('ts_posted', d.get('ts', datetime.now().isoformat()))), datetime.now().isoformat(), perc, title)
    
    # Track statistics for this video
    video_deleted_count = len(deletions)
    video_changes = video_new_comments + video_deleted_count
    
    if video_changes > 0:
        videos_with_changes += 1
        logging.info(f"Video [{v_id}] changes: +{video_new_comments} new, -{video_deleted_count} deleted")
    else:
        videos_with_no_changes += 1
    
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
    logging.info(f"Completed processing video '{v_id}' in {processing_time:.2f} seconds.")

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

# Move archived videos from active to archived list
if archived_video_ids:
    try:
        # Load current video data
        with open(VIDEO_LIST, "r") as f:
            data = json.load(f)
        
        active_videos = [vid for vid in data.get("active", []) if vid not in archived_video_ids]

        
        archived_videos = list(dict.fromkeys(data.get("archived", []) + archived_video_ids))
        
        # Save updated data with both active and archived
        updated_data = {
            "active": active_videos,
            "archived": archived_videos
        }
        
        with open(VIDEO_LIST, "w", encoding='utf-8') as f:
            json.dump(updated_data, f, indent=2)
        
        logging.info(f"Moved {len(archived_video_ids)} videos from active to archived in {VIDEO_LIST}")
        for archived_id in archived_video_ids:
            logging.info(f"  - Archived video: {archived_id}")
    except Exception as e:
        logging.warning(f"Failed to update {VIDEO_LIST}: {e}")

# Save history while preserving any cached published dates
try:
    # Load existing history to preserve cached published fields
    existing_history = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding='utf-8') as f:
                existing_history = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            existing_history = {}
    
    # Merge current history with existing cached data
    for v_id, existing_data in existing_history.items():
        if v_id in history:
            # Preserve published field from existing data if current doesn't have it
            if 'published' in existing_data and 'published' not in history[v_id]:
                history[v_id]['published'] = existing_data['published']
        else:
            # Preserve entire existing entry if not in current history
            history[v_id] = existing_data
    
    with open(STATE_FILE, "w", encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    logging.info("Comment state saved. Monitoring complete.")
except Exception as e:
    logging.error(f"Failed to save comment state: {e}")
