"""yt-dlp based extractors for supported networks without custom metadata APIs."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from apps.telegram_bot.errors import UnsupportedUrl
from apps.telegram_bot.extractors.base import ExtractionContext, SocialExtractor
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.download_manager import DownloadManager, detect_platform
from packages.url_utils import preparar_url_download_generico


GENERIC_PLATFORMS = frozenset({"youtube", "tiktok", "threads", "pinterest"})
_CONTENT_PATTERNS = {
    "youtube": re.compile(r"/watch(?:/|\?)|/(?:shorts|live)/|youtu\.be/", re.IGNORECASE),
    "tiktok": re.compile(r"/@[^/]+/(?:video|photo)/\d+|/t/[A-Za-z0-9]+", re.IGNORECASE),
    "threads": re.compile(r"/@[^/]+/post/[A-Za-z0-9_-]+|/t/[A-Za-z0-9_-]+", re.IGNORECASE),
    "pinterest": re.compile(r"/pin/\d+", re.IGNORECASE),
}
_PLAYLIST_PLATFORMS = {"tiktok", "threads", "pinterest"}


def is_generic_content_url(url: str) -> bool:
    platform = detect_platform(url)
    if platform not in GENERIC_PLATFORMS:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if platform == "tiktok" and host in {"t.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}:
        return bool(parsed.path.strip("/"))
    if platform == "pinterest" and (host == "pin.it" or host.endswith(".pin.it")):
        return bool(parsed.path.strip("/"))
    searchable = f"{parsed.path}?{parsed.query}"
    if platform == "youtube" and host == "youtu.be":
        searchable = f"youtu.be{parsed.path}"
    return bool(_CONTENT_PATTERNS[platform].search(searchable))


class GenericYtDlpExtractor(SocialExtractor):
    platform = "generic"

    def __init__(self, download_manager: DownloadManager) -> None:
        self.download_manager = download_manager

    def supports(self, url: str) -> bool:
        return is_generic_content_url(url)

    async def extract(
        self,
        url: str,
        *,
        context: ExtractionContext | None = None,
    ) -> MediaBundle:
        if not self.supports(url):
            raise UnsupportedUrl("URL generica nao suportada", platform=detect_platform(url))
        platform = detect_platform(url)
        normalized = preparar_url_download_generico(url)
        options = {
            "platform": platform,
            "allow_playlist": platform in _PLAYLIST_PLATFORMS,
            "playlist_limit": 20,
        }
        if context is not None:
            options.update(
                playlist_limit=context.playlist_limit,
                duration_limit=context.duration_limit,
                status=context.status,
                cancel_event=context.cancel_event,
                reply_markup=context.reply_markup,
            )
        return await self.download_manager.download(normalized, **options)


GenericExtractor = GenericYtDlpExtractor

__all__ = [
    "GENERIC_PLATFORMS",
    "GenericExtractor",
    "GenericYtDlpExtractor",
    "is_generic_content_url",
]
