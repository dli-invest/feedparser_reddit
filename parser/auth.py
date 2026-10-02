import asyncio
import logging
import sys
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from finreddit.config import RedditConfig

logger = logging.getLogger("finreddit.auth")

async def is_user_logged_in(tab) -> bool:
    """
    Checks multiple signals in Reddit DOM to verify if a valid session exists.
    """
    try:
        # Check 1: Reddit Session check via JS window context
        script = """
        () => {
            if (document.querySelector('button[id*="user-drawer"]')) return true;
            if (document.querySelector('shreddit-profile-dropdown')) return true;
            if (document.querySelector('#USER_DROPDOWN_ID')) return true;
            if (document.querySelector('a[href*="/user/"] img')) return true;
            return false;
        }
        """
        logged_in = await tab.execute_script(f"return ({script})()")
        if logged_in:
            return True

        # Check 2: Check current URL
        current_url = getattr(tab, "current_url", "")
        if "/login" not in current_url and "/register" not in current_url:
            # If we are on reddit home or a subreddit and not seeing "Log In" buttons
            login_btn = await tab.find(selector='a[href*="/login"], button[aria-label="Log in"]')
            if not login_btn:
                return True
    except Exception as e:
        logger.debug(f"Session check error: {e}")
    return False


async def wait_for_user_intervention(tab, reason: str):
    """
    Holds off scraping and prompts the user to resolve issues directly in the browser.
    Auto-polls login state so the script resumes once resolved.
    """
    logger.warning("=" * 70)
    logger.warning("ACTION REQUIRED: Scraping paused due to unexpected login state.")
    logger.warning(f"Reason: {reason}")
    logger.warning("The Chrome browser has been opened for manual intervention.")
    logger.warning("Please complete login / solve any CAPTCHA or 2FA in the browser.")
    logger.warning("=" * 70)

    # Polling loop: checks every 3 seconds if the user successfully logged in
    for _ in range(120): # Wait up to 6 minutes
        if await is_user_logged_in(tab):
            logger.info("Login verified successfully via browser interaction!")
            return True

        # Check if stdin is interactive (console run)
        if sys.stdin.isatty():
            try:
                # Non-blocking wait helper
                await asyncio.sleep(2)
            except Exception:
                pass
        else:
            await asyncio.sleep(3)

    raise TimeoutError("Timed out waiting for user intervention.")


async def login_to_reddit(browser, tab, config: RedditConfig) -> bool:
    """
    Logs into Reddit using credentials and handles unexpected hurdles gracefully.
    """
    logger.info("Navigating to Reddit login...")
    await tab.go_to("https://www.reddit.com/login")
    await asyncio.sleep(3)

    # 1. Check if user is already logged in (via persistent profile)
    if await is_user_logged_in(tab):
        logger.info("User session already active. Skipping login form.")
        return True

    logger.info("Filling in credentials with humanized behavior...")
    try:
        # Username selection (covers multiple Reddit UI variants)
        username_input = None
        for selector in ['#login-username', 'input[name="username"]', '#loginUsername']:
            try:
                username_input = await tab.find(selector=selector, timeout=3)
                if username_input:
                    break
            except Exception:
                continue

        if not username_input:
            await wait_for_user_intervention(tab, "Could not find username input field.")
            return True

        await username_input.type_text(config.username, humanize=True)
        await asyncio.sleep(1)

        # Password selection
        password_input = None
        for selector in ['#login-password', 'input[name="password"]', '#loginPassword']:
            try:
                password_input = await tab.find(selector=selector, timeout=3)
                if password_input:
                    break
            except Exception:
                continue

        if not password_input:
            await wait_for_user_intervention(tab, "Could not find password input field.")
            return True

        await password_input.type_text(config.password, humanize=True)
        await asyncio.sleep(1)

        # Submit button
        login_btn = None
        for selector in ['button[type="submit"]', 'button.login', 'button[data-testid="login-button"]']:
            try:
                login_btn = await tab.find(selector=selector, timeout=3)
                if login_btn:
                    break
            except Exception:
                continue

        if not login_btn:
            await wait_for_user_intervention(tab, "Could not find submit button.")
            return True

        await login_btn.click(humanize=True)
        logger.info("Submitted login credentials. Waiting for response...")
        await asyncio.sleep(6)

    except Exception as e:
        logger.error(f"Unexpected error during credential injection: {e}")
        await wait_for_user_intervention(tab, f"Exception during form submission: {e}")
        return True

    # 2. Verify login status
    if await is_user_logged_in(tab):
        logger.info("Reddit login successfully confirmed!")
        return True

    # 3. Detect CAPTCHA, 2FA, or invalid password errors
    page_text = await tab.execute_script("return document.body.innerText") or ""
    page_text_lower = page_text.lower()

    if "incorrect username or password" in page_text_lower:
        await wait_for_user_intervention(tab, "Incorrect username or password provided.")
    elif "verify you are human" in page_text_lower or "captcha" in page_text_lower:
        await wait_for_user_intervention(tab, "CAPTCHA verification detected.")
    elif "two-factor" in page_text_lower or "verification code" in page_text_lower:
        await wait_for_user_intervention(tab, "Two-Factor Authentication (2FA) prompt detected.")
    else:
        await wait_for_user_intervention(tab, "Login did not redirect to authenticated session.")

    return True