import re
from urllib.parse import urlparse, urlunparse


def normalizar_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        tweet_match = re.search(r"status/(\d+)", url)
        if tweet_match:
            return f"tweet:{tweet_match.group(1)}"
        limpo = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return limpo.rstrip("/").lower()
    except Exception:
        return url.lower().strip()


def normalizar_link_social(url_raw: str) -> str:
    url_norm = urlunparse(urlparse(url_raw)._replace(query="")).lower().rstrip("/")
    tw_match = re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", url_norm)
    if tw_match:
        return f"https://x.com/i/status/{tw_match.group(1)}"
    return url_norm
