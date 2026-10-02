import logging
import requests
import time
from typing import List, Dict, Any
from finreddit.config import DiscordConfig

logger = logging.getLogger("finreddit.notifier")

def send_discord_alerts(posts: List[Dict[str, Any]], discord_cfg: DiscordConfig):
    if not discord_cfg.enabled or not discord_cfg.webhook_url:
        logger.info("Discord notification disabled or webhook URL not set.")
        return

    if not posts:
        logger.info("No posts to send to Discord.")
        return

    logger.info(f"Sending {len(posts)} alerts to Discord...")

    for post in posts:
        # Truncate content to avoid exceeding Discord embed limits (4096 chars)
        content_preview = post.get("content", "")
        if len(content_preview) > 500:
            content_preview = content_preview[:497] + "..."

        embed = {
            "title": post.get("title", "Reddit Post")[:250],
            "url": f"https://reddit.com{post.get('permalink', '')}",
            "description": content_preview or "_[Link or Image Post]_",
            "color": discord_cfg.embed_color,
            "fields": [
                {"name": "Subreddit", "value": f"r/{post.get('subreddit')}", "inline": True},
                {"name": "Score", "value": str(post.get("score", 0)), "inline": True},
                {"name": "Comments", "value": str(post.get("num_comments", 0)), "inline": True},
                {"name": "Matched", "value": ", ".join(post.get("matched_criteria", [])) or "None", "inline": False},
            ],
            "footer": {"text": f"FinReddit PyDoll Scanner • Author: u/{post.get('author')}"}
        }

        payload = {
            "username": "FinReddit Bot",
            "embeds": [embed]
        }

        try:
            resp = requests.post(discord_cfg.webhook_url, json=payload, timeout=10)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2)
                time.sleep(retry_after)
                requests.post(discord_cfg.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            time.sleep(1) # Prevent flooding Discord's API
        except Exception as e:
            logger.error(f"Failed to post to Discord webhook: {e}")