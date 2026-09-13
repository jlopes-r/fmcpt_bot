"""Ordered registry that selects exactly one extractor for a social URL."""

from __future__ import annotations

import os
from collections.abc import Iterable

import aiohttp

from apps.telegram_bot.errors import UnsupportedUrl
from apps.telegram_bot.extractors.base import SocialExtractor
from apps.telegram_bot.extractors.facebook import FacebookExtractor
from apps.telegram_bot.extractors.generic import GenericYtDlpExtractor
from apps.telegram_bot.extractors.instagram import InstagramExtractor
from apps.telegram_bot.extractors.twitter import TwitterExtractor
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.download_manager import DownloadManager


class ExtractorRegistry:
    def __init__(self, extractors: Iterable[SocialExtractor] = ()) -> None:
        self._extractors: list[SocialExtractor] = list(extractors)

    @property
    def extractors(self) -> tuple[SocialExtractor, ...]:
        return tuple(self._extractors)

    def register(self, extractor: SocialExtractor, *, prepend: bool = False) -> None:
        if extractor in self._extractors:
            raise ValueError("Extrator ja registrado")
        if prepend:
            self._extractors.insert(0, extractor)
        else:
            self._extractors.append(extractor)

    def resolve(self, url: str) -> SocialExtractor:
        for extractor in self._extractors:
            try:
                if extractor.supports(url):
                    return extractor
            except (TypeError, ValueError):
                continue
        raise UnsupportedUrl("Nenhum extrator reconheceu a URL", stage="routing")

    async def extract(self, url: str) -> MediaBundle:
        return await self.resolve(url).extract(url)


def build_default_registry(
    session: aiohttp.ClientSession,
    download_manager: DownloadManager,
    *,
    instagram_cookie_path: str | None = None,
    instagram_secondary_cookie_path: str | None = None,
) -> ExtractorRegistry:
    """Build the production order: custom extractors before generic yt-dlp."""
    return ExtractorRegistry(
        (
            InstagramExtractor(
                download_manager,
                cookie_path=instagram_cookie_path
                if instagram_cookie_path is not None
                else os.getenv("IG_COOKIE_PATH", ""),
                secondary_cookie_path=instagram_secondary_cookie_path
                if instagram_secondary_cookie_path is not None
                else os.getenv("IG_SECONDARY_COOKIE_PATH", ""),
            ),
            FacebookExtractor(session, download_manager),
            TwitterExtractor(session, download_manager),
            GenericYtDlpExtractor(download_manager),
        )
    )


__all__ = ["ExtractorRegistry", "build_default_registry"]
