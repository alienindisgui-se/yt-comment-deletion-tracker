import yt_dlp
import json
import os
import requests
import time
import random
import sys
import logging
import socket

sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
VIDEO_LIST = "videos.json"
STATE_FILE = "comment_state.json"
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
if WEBHOOK is None and os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            if line.startswith('DISCORD_WEBHOOK='):
                WEBHOOK = line.split('=', 1)[1].strip()
                break

# Validate webhook URL
if WEBHOOK and not WEBHOOK.startswith('https://discord.com/api/webhooks/'):
    logging.error(f"Invalid Discord webhook URL format: {WEBHOOK}")
    WEBHOOK = None

def check_po_token_server():
    """Check if the PO token server is running on localhost:4416"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', 4416))
        sock.close()
        return result == 0
    except:
        return False
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 13; Mobile; rv:123.0) Gecko/123.0 Firefox/123.0",
    "Mozilla/5.0 (Android 13; Mobile; rv:122.0) Gecko/122.0 Firefox/122.0",
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36"
]

def get_yt_data(v_id, deep_scrape=False):
    time.sleep(random.uniform(10, 30)) # Random delay to look human
    user_agent = random.choice(USER_AGENTS)
    opts = {
        'getcomments': deep_scrape,
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'user_agent': user_agent,
        'no_warnings': True,
        'no_cookies': True
    }
    if check_po_token_server():
        opts['extractor_args'] = {'youtube': {'po_token': ['web+http://127.0.0.1:4416']}}
    else:
        logging.info("PO token server not available, proceeding without PO tokens")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={v_id}", download=False)
            count = info.get('comment_count', 0)
            title = info.get('title', 'Unknown')
            comments = {c['id']: {'a': c['author'], 't': c['text'], 'ts': c['timestamp']} for c in info.get('comments', [])} if deep_scrape else None
            return count, comments, title
    except Exception as e:
        error_str = str(e)
        if 'Sign in to confirm you’re not a bot' in error_str:
            logging.error(f"Bot detection triggered for {v_id}: {error_str}")
            sys.exit(1)
        if '403' in error_str or 'Forbidden' in error_str or 'throttle' in error_str.lower():
            print(f"WARNING: Possible throttling or ban detected for {v_id}: {error_str}")
        elif '127.0.0.1:4416' in error_str or 'po_token' in error_str.lower():
            logging.warning(f"PO token service failure for {v_id}: {error_str}")
        else:
            print(f"Error fetching {v_id}: {error_str}")
        return None, None, None

def send_deletion_alert(author, text, v_id, ts, deleted_at, percentage, title):
    if not WEBHOOK:
        logging.error("Discord webhook not configured, cannot send notification")
        return
    url = f"https://www.youtube.com/watch?v={v_id}"
    if percentage <= 25:
        color = 0xFFEB3B
    elif percentage <= 50:
        color = 0xFFC107
    elif percentage <= 75:
        color = 0xFF7043
    else:
        color = 0xD32F2F
    payload = {
        "embeds": [{
            "title": "🚨 Deleted Comment Detected",
            "description": f"**Author:** `{author}`\n**Content:** {text[:800]}\n**Posted:** <t:{int(ts)}:f>\n**Deleted:** <t:{int(deleted_at)}:f>\n\n**{percentage:.1f}%** of the registered comments on this video has been removed.",
            "color": color,
            "fields": [{"name": title, "value": f"[View Video]({url})", "inline": True}],
            "footer": {"text": f"Video ID: {v_id} | [yt-comment-deletion-tracker](https://github.com/alienindisgui-se/yt-comment-deletion-tracker)"}
        }]
    }
    requests.post(WEBHOOK, json=payload)

# START PROCESS
if not os.path.exists(VIDEO_LIST):
    with open(VIDEO_LIST, "w") as f: json.dump(["dQw4w9WgXcQ"], f)
    exit("videos.json created. Add your IDs and run again.")

with open(VIDEO_LIST, "r") as f: video_ids = json.load(f)

history_exists = os.path.exists(STATE_FILE)
history = {}
if history_exists:
    with open(STATE_FILE, "r", encoding='utf-8') as f:
        history = json.load(f)

CHECK_BATCH = 10
sorted_vids = sorted(video_ids, key=lambda v: history.get(v, {}).get('last_checked', 0))
video_ids_to_check = sorted_vids[:CHECK_BATCH]

for v_id in video_ids_to_check:
    if len(v_id) != 11:
        print(f"Skipping invalid video ID: {v_id}")
        continue
    current_count, _, title = get_yt_data(v_id, deep_scrape=False)
    if current_count is None:
        print(f"WARNING: Skipping {v_id} due to fetch failure")
        continue
    else:
        print(f"Checking: {title} [{v_id}]")

    video_title = title if current_count is not None else None

    old_data = history.get(v_id, {"count": -1, "comments": {}})
    
    if v_id not in history:
        history[v_id] = {"count": old_data["count"], "comments": old_data["comments"], "deletions": []}
    
    # If the count changed, we need to see what's missing
    if current_count != old_data["count"]:
        _, current_comments, title = get_yt_data(v_id, deep_scrape=True)
        if current_comments is None:
            print(f"WARNING: Skipping comment comparison for {v_id} due to fetch failure")
            continue

        deletions = []

        # Only notify if we have a previous state to compare to
        if history_exists and old_data["comments"]:
            for c_id, data in old_data["comments"].items():
                if c_id not in current_comments:
                    deletions.append({'id': c_id, 'a': data['a'], 't': data['t'], 'ts': data.get('ts', 0), 'deleted_at': time.time()})

        history[v_id] = {"count": current_count, "comments": current_comments, "deletions": deletions, "title": title}

    history[v_id]['last_checked'] = time.time()

for v_id, data in history.items():
    deletions = data.get('deletions', [])
    if deletions:
        total_registered = len(data.get('comments', {}))
        removed_count = len(deletions)
        percentage = (removed_count / total_registered * 100) if total_registered > 0 else 0
        title = data.get('title', f'Video {v_id}')
        for deletion in deletions:
            send_deletion_alert(deletion['a'], deletion['t'], v_id, deletion['ts'], deletion['deleted_at'], percentage, title)

with open(STATE_FILE, "w", encoding='utf-8') as f: json.dump(history, f, indent=2, ensure_ascii=False)
