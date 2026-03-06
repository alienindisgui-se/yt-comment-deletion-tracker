import json
import random
import sys
import logging
from playwright.sync_api import sync_playwright, TimeoutError

# Setup logging and encoding
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
VIDEO_LIST = "videos.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def fetch_latest_videos(channels):
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
                logging.info(f"Fetching latest video for channel: {channel}")
                page.goto(url, timeout=60000)
                page.wait_for_load_state('networkidle')
                logging.info(f"Page loaded, title: {page.title()}")
                
                # Handle YouTube consent page
                if "Before you continue to YouTube" in page.title():
                    logging.info("Consent page detected, attempting to accept...")
                    try:
                        accept_button = page.locator('button').filter(has_text="Accept all").first
                        if accept_button.count() > 0:
                            accept_button.click()
                            page.wait_for_timeout(2000)
                            page.wait_for_load_state('networkidle')
                            logging.info("Consent accepted, page reloaded.")
                        else:
                            logging.warning("Accept button not found on consent page.")
                    except Exception as e:
                        logging.error(f"Failed to accept consent: {e}")
                
                # Scroll to load videos
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(5000)
                
                # Find the first video link
                rich_count = page.locator('ytd-rich-item-renderer').count()
                logging.info(f"Found {rich_count} ytd-rich-item-renderer elements")
                link_count = page.locator('ytd-rich-item-renderer a[href*="/watch?v="]').count()
                logging.info(f"Found {link_count} video links in rich items")
                video_locator = page.locator('ytd-rich-item-renderer a[href*="/watch?v="]').first
                if video_locator.count() > 0:
                    href = video_locator.get_attribute('href')
                    logging.info(f"First video href: {href}")
                    if href and 'v=' in href:
                        v_id = href.split('v=')[1].split('&')[0]
                        latest_videos.append(v_id)
                        logging.info(f"Fetched latest video {v_id} for channel {channel}")
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

# --- MAIN LOGIC ---
logging.info("Starting YouTube latest video fetcher.")
channels = ['CarlFredrikAlexanderRask', 'ANJO1', 'MotVikten', 'Skuldis']
video_ids = fetch_latest_videos(channels)
if not video_ids:
    logging.warning("No videos fetched from channels. Exiting.")
    sys.exit()
with open(VIDEO_LIST, "w", encoding='utf-8') as f:
    json.dump(video_ids, f, indent=2)
logging.info("Videos saved.")