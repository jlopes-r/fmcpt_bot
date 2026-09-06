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


def preparar_url_download_generico(url: str) -> str:
    """Normaliza URLs sem remover identificadores necessários ao conteúdo."""
    yt_match = re.search(r'(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]+)', url)
    if yt_match:
        return f"https://www.youtube.com/watch?v={yt_match.group(1)}"
    # Alguns links do Facebook identificam o vídeo na query (video.php?v=...).
    # O yt-dlp também resolve sozinho links curtos de fb.watch.
    if any(d in url for d in ("facebook.com", "fb.com", "fb.watch")):
        return url
    return urlunparse(urlparse(url)._replace(query="")).rstrip("/")
