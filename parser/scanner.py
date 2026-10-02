import asyncio
import logging
import re
import random
from typing import List, Dict, Any, Set
from finreddit.config import AppConfig, FilterConfig
from finreddit.auth import login_to_reddit, wait_for_user_intervention
from finreddit.storage import append_posts_to_csv
from finreddit.notifier import send_discord_alerts
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

logger = logging.getLogger("finreddit.scanner")


def matches_criteria(post: Dict[str, Any], filters: FilterConfig) -> List[str]:
    """
    Evaluates whether a DOM-extracted post meets score, comment,
    flair, ticker, and keyword thresholds.
    """
    if post.get("score", 0) < filters.min_score:
        return []
    if post.get("num_comments", 0) < filters.min_comments:
        return []

    title = post.get("title", "")
    content = post.get("content", "")
    flair = post.get("flair", "")
    combined_text = f"{title} {content} {flair}".lower()

    matched: List[str] = []

    # Check Keywords (case-insensitive)
    for kw in filters.keywords:
        if kw.lower() in combined_text:
            matched.append(kw)

    # Check Tickers with word boundaries (e.g. \bNVDA\b)
    for ticker in filters.tickers:
        pattern = rf"\b{re.escape(ticker)}\b"
        if re.search(pattern, title, re.IGNORECASE) or re.search(pattern, content, re.IGNORECASE):
            matched.append(f"${ticker.upper()}")

    # If no filters were specified, let posts passing score/comments pass
    if not filters.keywords and not filters.tickers:
        return ["passed_thresholds"]

    return list(set(matched))


def build_subreddit_url(sub_name: str, sort: str) -> str:
    """Builds the canonical Reddit desktop URL for the given sort."""
    sub_clean = sub_name.strip().lstrip("r/").rstrip("/")
    sort_clean = sort.strip().lower()

    if sort_clean == "top":
        return f"https://www.reddit.com/r/{sub_clean}/top/?t=day"
    elif sort_clean in ("new", "rising", "hot"):
        return f"https://www.reddit.com/r/{sub_clean}/{sort_clean}/"
    return f"https://www.reddit.com/r/{sub_clean}/"


async def check_for_page_blocks(tab) -> bool:
    """
    Detects if Reddit presented an anti-bot screen, rate limit,
    or CAPTCHA interstitial during navigation.
    """
    try:
        page_text = await tab.execute_script(
            "return (document.body ? document.body.innerText : '').toLowerCase();"
        ) or ""

        block_signatures = [
            "whoa there, slow down",
            "verify you are human",
            "blocked",
            "unusual traffic",
            "access denied"
        ]
        return any(sig in page_text for sig in block_signatures)
    except Exception:
        return False


async def extract_visible_posts_from_dom(tab) -> List[Dict[str, Any]]:
    """
    Pure DOM extractor executing in the tab context. Supports:
    1. Modern Reddit (`shreddit-post` web components + slotted body content)
    2. Classic / Old Reddit (`div.thing`)
    3. Generic article tags fallback
    """
    extraction_script = """
    () => {
        function parseMetric(val) {
            if (!val) return 0;
            val = val.toString().trim().toLowerCase();
            if (val.endsWith('k')) {
                return Math.round(parseFloat(val.replace('k', '')) * 1000);
            }
            if (val.endsWith('m')) {
                return Math.round(parseFloat(val.replace('m', '')) * 1000000);
            }
            const clean = parseInt(val.replace(/[^0-9-]/g, ''), 10);
            return isNaN(clean) ? 0 : clean;
        }

        const items = [];

        // --- Layout 1: Modern Reddit (shreddit-post web components) ---
        const shredditPosts = document.querySelectorAll('shreddit-post');
        if (shredditPosts.length > 0) {
            shredditPosts.forEach(p => {
                const id = p.getAttribute('id') || p.getAttribute('data-fullname') || '';
                const permalink = p.getAttribute('permalink') || '';
                const title = p.getAttribute('post-title') || 
                              p.querySelector('a[slot="title"]')?.innerText || 
                              p.querySelector('[slot="title"]')?.innerText || '';
                const author = p.getAttribute('author') || '';
                const score = parseMetric(p.getAttribute('score'));
                const comments = parseMetric(p.getAttribute('comment-count'));
                const contentHref = p.getAttribute('content-href') || '';
                const subreddit = p.getAttribute('subreddit-prefixed-name') || '';

                // Extract slotted text content (selftext/body)
                const bodySlot = p.querySelector('[slot="text-body"]') || 
                                 p.querySelector('div[id$="-post-rtjson-content"]') ||
                                 p.querySelector('.md');
                const content = bodySlot ? bodySlot.innerText.trim() : '';

                // Extract post flair (e.g. DD, Discussion, News)
                const flairEl = p.querySelector('shreddit-post-flair');
                const flair = flairEl ? flairEl.innerText.trim() : '';

                if (id || permalink || title) {
                    items.push({
                        id: id,
                        subreddit: subreddit.replace(/^r\\//, ''),
                        title: title.trim(),
                        author: author,
                        score: score,
                        num_comments: comments,
                        url: contentHref || (permalink ? `https://www.reddit.com${permalink}` : ''),
                        permalink: permalink,
                        flair: flair,
                        content: content
                    });
                }
            });
            return items;
        }

        // --- Layout 2: Classic / Legacy Reddit (div.thing) ---
        const oldPosts = document.querySelectorAll('div.thing');
        if (oldPosts.length > 0) {
            oldPosts.forEach(p => {
                const id = p.getAttribute('data-fullname') || p.id || '';
                const titleEl = p.querySelector('a.title');
                const title = titleEl ? titleEl.innerText.trim() : '';
                const author = p.getAttribute('data-author') || '';
                const score = parseMetric(p.getAttribute('data-score'));
                const comments = parseMetric(p.getAttribute('data-comments-count'));
                const permalink = p.getAttribute('data-permalink') || '';
                const url = p.getAttribute('data-url') || '';
                const expando = p.querySelector('div.expando .usertext-body');
                const content = expando ? expando.innerText.trim() : '';

                if (id || permalink) {
                    items.push({
                        id: id,
                        subreddit: (p.getAttribute('data-subreddit') || '').trim(),
                        title: title,
                        author: author,
                        score: score,
                        num_comments: comments,
                        url: url,
                        permalink: permalink,
                        flair: '',
                        content: content
                    });
                }
            });
            return items;
        }

        // --- Layout 3: Generic Container Fallback ---
        const articles = document.querySelectorAll('article');
        articles.forEach((art, idx) => {
            const titleEl = art.querySelector('h1, h2, h3, a[data-testid="post-title"]');
            const linkEl = art.querySelector('a[href*="/comments/"]');
            const permalink = linkEl ? linkEl.getAttribute('href') : '';
            const bodyEl = art.querySelector('[data-click-id="text_body"], p');

            if (titleEl) {
                items.push({
                    id: `art_${idx}`,
                    subreddit: '',
                    title: titleEl.innerText.trim(),
                    author: '',
                    score: 0,
                    num_comments: 0,
                    url: permalink ? (permalink.startsWith('http') ? permalink : `https://www.reddit.com${permalink}`) : '',
                    permalink: permalink,
                    flair: '',
                    content: bodyEl ? bodyEl.innerText.trim() : ''
                });
            }
        });

        return items;
    }
    """
    try:
        raw_items = await tab.execute_script(f"return ({extraction_script})()")
        return raw_items or []
    except Exception as e:
        logger.warning(f"Error executing DOM extraction script: {e}")
        return []


async def enrich_matched_post_content(tab, post: Dict[str, Any]) -> str:
    """
    If a post matches criteria but its body content in the feed was truncated or empty,
    navigates to the permalink to retrieve the complete text content for the CSV.
    """
    if not post.get("permalink"):
        return post.get("content", "")

    full_url = f"https://www.reddit.com{post['permalink']}"
    try:
        await tab.go_to(full_url)
        await asyncio.sleep(2.5)

        script = """
        () => {
            const post = document.querySelector('shreddit-post');
            if (post) {
                const bodySlot = post.querySelector('[slot="text-body"]') || 
                                 post.querySelector('div[id$="-post-rtjson-content"]') ||
                                 post.querySelector('.md');
                if (bodySlot) return bodySlot.innerText.trim();
            }
            const fallbackBody = document.querySelector('[data-click-id="text_body"], .usertext-body');
            return fallbackBody ? fallbackBody.innerText.trim() : '';
        }
        """
        full_content = await tab.execute_script(f"return ({script})()")
        return full_content if full_content else post.get("content", "")
    except Exception as e:
        logger.debug(f"Failed to enrich post content for {post.get('id')}: {e}")
        return post.get("content", "")


async def fetch_subreddit_posts(tab, sub_name: str, sort: str, limit: int) -> List[Dict[str, Any]]:
    """
    Replaces JSON/RSS scraping. Navigates directly to the subreddit and
    performs progressive scrolling until `limit` posts are collected.
    """
    url = build_subreddit_url(sub_name, sort)
    logger.info(f"Navigating to r/{sub_name} ({sort}) -> {url}")
    await tab.go_to(url)
    await asyncio.sleep(4)

    # Interstitial / Block Check
    if await check_for_page_blocks(tab):
        await wait_for_user_intervention(tab, f"Anti-bot or rate-limit screen detected on r/{sub_name}")

    posts_by_key: Dict[str, Dict[str, Any]] = {}
    stagnant_scroll_count = 0
    max_scroll_attempts = max(15, (limit // 4) * 2)

    for attempt in range(max_scroll_attempts):
        visible_posts = await extract_visible_posts_from_dom(tab)
        new_found = 0

        for p in visible_posts:
            # Key by unique ID or permalink
            key = p.get("id") or p.get("permalink") or p.get("title")
            if key and key not in posts_by_key:
                if not p.get("subreddit"):
                    p["subreddit"] = sub_name
                posts_by_key[key] = p
                new_found += 1

        logger.debug(f"Scroll {attempt + 1}: Found {new_found} new posts (Total: {len(posts_by_id:=posts_by_key)}/{limit})")

        if len(posts_by_key) >= limit:
            break

        if new_found == 0:
            stagnant_scroll_count += 1
            if stagnant_scroll_count >= 3:
                logger.info(f"Feed reached end or stopped rendering new items after {attempt + 1} scrolls.")
                break
        else:
            stagnant_scroll_count = 0

        # Human-like progressive scrolling
        scroll_px = random.randint(750, 1100)
        await tab.execute_script(f"window.scrollBy({{ top: {scroll_px}, behavior: 'smooth' }});")
        await asyncio.sleep(random.uniform(1.4, 2.3))

    results = list(posts_by_key.values())[:limit]
    logger.info(f"Extracted {len(results)} posts from DOM for r/{sub_name}")
    return results


async def scan_subreddits_workflow(config: AppConfig):
    """
    Main scraping loop: launches Chrome via PyDoll, verifies authentication,
    scrapes target subreddits using pure DOM inspection, enriches matches,
    and exports to CSV & Discord.
    """
    options = ChromiumOptions()
    if config.reddit.user_data_dir:
        # Retains cookies & local storage between Windows Task Scheduler runs
        options.add_argument(f"--user-data-dir={config.reddit.user_data_dir}")

    logger.info("Starting PyDoll Chromium session...")
    async with Chrome(options=options) as browser:
        tab = await browser.start(headless=config.reddit.headless)

        # 1. Login & verification
        await login_to_reddit(browser, tab, config.reddit)

        all_matched_posts: List[Dict[str, Any]] = []
        seen_keys: Set[str] = set()

        # 2. Iterate Subreddits via DOM
        for sr in config.subreddits:
            posts = await fetch_subreddit_posts(tab, sr.name, sr.sort, sr.limit)

            matched_in_sr = []
            for post in posts:
                key = post.get("id") or post.get("permalink")
                if key in seen_keys:
                    continue

                matched = matches_criteria(post, config.filters)
                if matched:
                    seen_keys.add(key)
                    post["matched_criteria"] = matched
                    matched_in_sr.append(post)

            # 3. Enrich content for matched posts if feed truncated the text
            for m_post in matched_in_sr:
                # If post content is empty or short, fetch full post body from permalink
                if len(m_post.get("content", "")) < 80 and m_post.get("permalink"):
                    m_post["content"] = await enrich_matched_post_content(tab, m_post)
                all_matched_posts.append(m_post)

        logger.info(f"Total matching posts across all subreddits: {len(all_matched_posts)}")

        # 4. Save results locally in CSV (with complete content body)
        append_posts_to_csv(all_matched_posts, config.storage.csv_file)

        # 5. Send alerts to Discord
        send_discord_alerts(all_matched_posts, config.discord)


def ScanSRs(config_path: str):
    """
    Direct Python equivalent to finreddit's ScanSRs.
    """
    from finreddit.config import load_config
    config = load_config(config_path)
    asyncio.run(scan_subreddits_workflow(config))