# YT Comment Deletion Tracker
A stealthy monitor for tracking deleted YouTube comments.

### How it works
1. Runs every 20 minutes via **GitHub Actions**.
2. Checks comment counts. If a change is found, it performs a deep diff.
3. Sends a **Discord notification** if a previously existing comment is missing.

### Setup
1. Create a Discord Webhook.
2. Add the URL to GitHub Secrets as `DISCORD_WEBHOOK`.
3. Add video IDs to `videos.json`.
