"""Conservative, best-effort classification; never retain the input header."""

import re


BOT_PATTERN = re.compile(
    r"telegrambot|googlebot|bingbot|bingpreview|duckduckbot|baiduspider|"
    r"yandexbot|applebot|slurp|facebookexternalhit|facebot|twitterbot|"
    r"slackbot|discordbot|linkedinbot|whatsapp|pinterestbot|petalbot|"
    r"ahrefsbot|semrushbot|gptbot|oai-searchbot|chatgpt-user|claudebot|"
    r"headlesschrome|selenium|playwright|curl/|wget/|python-requests/|"
    r"python-urllib/|aiohttp/|python-httpx/",
    re.IGNORECASE,
)
BROWSER_PATTERN = re.compile(r"(?:chrome|crios|firefox|fxios|safari|edg|opr)/\d", re.IGNORECASE)


def classify_user_agent(user_agent):
    """Return only normalized fields. A browser signature is not proof of a human."""
    ua = (user_agent or "").lower()
    ios = any(marker in ua for marker in ("iphone", "ipad", "ipod")) or (
        "macintosh" in ua and "mobile/" in ua
    )
    if ios:
        operating_system = "iOS"
    elif "android" in ua:
        operating_system = "Android"
    elif "windows" in ua:
        operating_system = "Windows"
    elif "macintosh" in ua or "mac os x" in ua:
        operating_system = "macOS"
    elif "linux" in ua:
        operating_system = "Linux"
    else:
        operating_system = "Unknown"

    browser = "mozilla/5.0" in ua and BROWSER_PATTERN.search(ua) is not None
    if (
        any(marker in ua for marker in ("ipad", "tablet", "kindle", "silk/"))
        or ("macintosh" in ua and ios)
        or ("android" in ua and "mobile" not in ua)
    ):
        device_type = "Tablet"
    elif any(marker in ua for marker in ("iphone", "ipod", "mobile")):
        device_type = "Mobile"
    elif browser and operating_system in ("Windows", "macOS", "Linux"):
        device_type = "Desktop"
    else:
        device_type = "Unknown"

    if BOT_PATTERN.search(ua):
        is_bot = 1
    elif browser and operating_system != "Unknown":
        is_bot = 0
    else:
        is_bot = None

    return {
        "device_type": device_type,
        "operating_system": operating_system,
        "is_bot": is_bot,
    }
