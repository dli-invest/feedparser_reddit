import csv
import os
from datetime import datetime
from typing import List, Dict, Any
import logging

logger = logging.getLogger("finreddit.storage")

CSV_HEADERS = [
    "RunTimestamp",
    "Subreddit",
    "PostID",
    "Title",
    "Author",
    "Score",
    "NumComments",
    "URL",
    "Permalink",
    "MatchedCriteria",
    "Content"
]

def append_posts_to_csv(posts: List[Dict[str, Any]], filepath: str):
    """
    Appends scanned and filtered Reddit posts along with their full content to CSV.
    """
    if not posts:
        logger.info("No posts to save to CSV.")
        return

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    file_exists = os.path.isfile(filepath)

    run_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    with open(filepath, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()

        for post in posts:
            writer.writerow({
                "RunTimestamp": run_time,
                "Subreddit": post.get("subreddit", ""),
                "PostID": post.get("id", ""),
                "Title": post.get("title", ""),
                "Author": post.get("author", ""),
                "Score": post.get("score", 0),
                "NumComments": post.get("num_comments", 0),
                "URL": post.get("url", ""),
                "Permalink": f"https://reddit.com{post.get('permalink', '')}",
                "MatchedCriteria": ", ".join(post.get("matched_criteria", [])),
                "Content": post.get("content", "").replace("\r\n", "\n")
            })

    logger.info(f"Successfully saved {len(posts)} posts to {filepath}")