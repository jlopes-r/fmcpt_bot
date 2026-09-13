"""Adapter from the mature Instagram account pool to ``MediaBundle``."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from apps.telegram_bot.errors import AuthenticationRequired, ContentUnavailable, UnsupportedUrl
from apps.telegram_bot.extractors.base import SocialExtractor
from apps.telegram_bot.instagram import download_instagram
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.download_manager import DownloadManager


InstagramDownload = Callable[..., Awaitable[dict[str, Any] | None]]
_CONTENT_RE = re.compile(
    r"/(?:p|reel|reels|ad|tv)/[A-Za-z0-9_-]+|"
    r"/stories/(?:highlights/)?[^/?#]+|/share/",
    re.IGNORECASE,
)


def instagram_content_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if "/stories/highlights/" in path:
        return "highlight"
    if "/stories/" in path:
        return "story"
    if "/reel/" in path or "/reels/" in path:
        return "reel"
    return "post"


def _instagram_source_id(url: str) -> str:
    match = re.search(
        r"/(?:p|reel|reels|ad|tv)/([A-Za-z0-9_-]+)|"
        r"/stories/(?:highlights/)?[^/?#]+(?:/([0-9]+))?",
        url,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return next((group for group in match.groups() if group), "")


class InstagramExtractor(SocialExtractor):
    platform = "instagram"

    def __init__(
        self,
        download_manager: DownloadManager,
        *,
        cookie_path: str = "",
        secondary_cookie_path: str = "",
        duration_limit: float | None = None,
        legacy_download: InstagramDownload = download_instagram,
    ) -> None:
        self.download_manager = download_manager
        self.cookie_path = cookie_path
        self.secondary_cookie_path = secondary_cookie_path
        self.duration_limit = duration_limit
        self.legacy_download = legacy_download

    def supports(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        valid_host = host == "instagr.am" or host == "instagram.com" or host.endswith(".instagram.com")
        return bool(valid_host and _CONTENT_RE.search(parsed.path))

    async def extract(self, url: str) -> MediaBundle:
        if not self.supports(url):
            raise UnsupportedUrl("URL de conteudo do Instagram invalida", platform="instagram")
        legacy_error = ""
        try:
            result = await self.legacy_download(
                url,
                self.cookie_path,
                str(self.download_manager.download_root),
                secondary_cookie_path=self.secondary_cookie_path,
            )
        except Exception as exc:
            result = None
            legacy_error = type(exc).__name__
        if result:
            bundle = MediaBundle.from_legacy_result(
                result,
                platform="instagram",
                source_url=url,
            )
            expected = bundle.expected_items
            if expected is None:
                expected = len(result.get("files") or result.get("urls") or [])
            return replace(
                bundle,
                source_id=bundle.source_id or _instagram_source_id(url),
                expected_items=expected,
                metadata={
                    **bundle.metadata,
                    "content_type": instagram_content_type(url),
                    "extraction_strategy": "instagram-account-pool",
                },
            )

        try:
            options = {
                "platform": "instagram",
                "allow_playlist": instagram_content_type(url) in {"story", "highlight"},
                "playlist_limit": 20,
            }
            if self.duration_limit is not None:
                options["duration_limit"] = self.duration_limit
            fallback = await self.download_manager.download(url, **options)
            return replace(
                fallback,
                source_id=fallback.source_id or _instagram_source_id(url),
                metadata={
                    **fallback.metadata,
                    "content_type": instagram_content_type(url),
                    "extraction_strategy": "yt-dlp-fallback",
                    "primary_error": legacy_error,
                },
            )
        except ContentUnavailable as exc:
            configured_cookies = any(
                path and Path(path).is_file()
                for path in (self.cookie_path, self.secondary_cookie_path)
            )
            if not configured_cookies:
                raise AuthenticationRequired(
                    "Instagram exige uma sessao valida",
                    platform="instagram",
                    stage="authentication",
                ) from exc
            raise


__all__ = ["InstagramExtractor", "instagram_content_type"]
