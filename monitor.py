import yt_dlp
import json
import os
import requests
import time
import random

# CONFIG
VIDEO_LIST = "videos.json"
STATE_FILE = "comment_state.json"
WEBHOOK = os.getenv("DISCORD_WEBHOOK")
if WEBHOOK is None and os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            if line.startswith('DISCORD_WEBHOOK='):
                WEBHOOK = line.split('=', 1)[1].strip()
                break
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def get_yt_data(v_id, deep_scrape=False):
    time.sleep(random.uniform(2, 5)) # Random delay to look human
    opts = {
        'getcomments': deep_scrape,
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
        'user_agent': USER_AGENT,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={v_id}", download=False)
            count = info.get('comment_count', 0)
            title = info.get('title', 'Unknown')
            comments = {c['id']: {'a': c['author'], 't': c['text'], 'ts': c['timestamp']} for c in info.get('comments', [])} if deep_scrape else None
            return count, comments, title
    except Exception as e:
        print(f"Error fetching {v_id}: {e}")
        return None, None, None

def send_deletion_alert(author, text, v_id, ts, deleted_at, percentage, title):
    url = f"https://www.youtube.com/watch?v={v_id}"
    payload = {
        "embeds": [{
            "title": "🗑️ Deleted Comment Detected",
            "description": f"**Author:** `{author}`\n**Content:** {text[:800]}\n**Posted:** <t:{int(ts)}:f>\n**Deleted:** <t:{int(deleted_at)}:f>\n\n{percentage:.1f}% of the registered comments on this video has been removed.",
            "color": 0xe74c3c,
            "fields": [{"name": title, "value": f"[View Video]({url})", "inline": True}],
            "footer": {"text": f"Video ID: {v_id} | Stealth Monitor 2026"}
        }]
    }
    requests.post(WEBHOOK, json=payload)

# START PROCESS
if not os.path.exists(VIDEO_LIST):
    with open(VIDEO_LIST, "w") as f: json.dump(["dQw4w9WgXcQ"], f)
    exit("videos.json created. Add your IDs and run again.")

with open(VIDEO_LIST, "r") as f: video_ids = json.load(f)

history_exists = os.path.exists(STATE_FILE)
history = json.load(open(STATE_FILE, "r", encoding='utf-8')) if history_exists else {}

CHECK_BATCH = 10
sorted_vids = sorted(video_ids, key=lambda v: history.get(v, {}).get('last_checked', 0))
video_ids_to_check = sorted_vids[:CHECK_BATCH]

for v_id in video_ids_to_check:
    print(f"Checking {v_id}...")
    current_count, _, _ = get_yt_data(v_id, deep_scrape=False)
    if current_count is None: continue
    
    old_data = history.get(v_id, {"count": -1, "comments": {}})
    
    if v_id not in history:
        history[v_id] = {"count": old_data["count"], "comments": old_data["comments"], "deletions": []}
    
    # If the count changed, we need to see what's missing
    if current_count != old_data["count"]:
        _, current_comments, title = get_yt_data(v_id, deep_scrape=True)
        if current_comments is None: continue

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
