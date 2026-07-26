import re


TWEET_URL_RE = re.compile(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)")


def match_tweet_url(url: str):
    return TWEET_URL_RE.search(url)


def build_vxtwitter_url(username: str, status_id: str) -> str:
    return f"https://api.vxtwitter.com/{username}/status/{status_id}"
