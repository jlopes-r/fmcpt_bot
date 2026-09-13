"""Extrator tipado para VXTwitter/FXTwitter com fallback unico pelo yt-dlp."""

from __future__ import annotations

import re
from typing import Any

import aiohttp

from apps.telegram_bot.errors import ContentUnavailable, RateLimited, UnsupportedUrl
from apps.telegram_bot.extractors.base import ExtractionContext, SocialExtractor
from apps.telegram_bot.models.media import MediaBundle, MediaItem
from apps.telegram_bot.services.download_manager import DownloadManager
from apps.telegram_bot.twitter import build_fxtwitter_url, build_vxtwitter_url, match_tweet_url


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _duration_seconds(media: dict) -> float | None:
    millis = media.get("duration_millis")
    if millis not in (None, ""):
        try:
            return float(millis) / 1000
        except (TypeError, ValueError):
            return None
    duration = media.get("duration")
    try:
        return float(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _best_media_url(media: dict) -> str:
    formats = media.get("formats") or []
    preferred = [
        item for item in formats
        if isinstance(item, dict)
        and item.get("url")
        and item.get("container") == "mp4"
        and item.get("codec") in {None, "", "h264"}
    ]
    if preferred:
        preferred.sort(
            key=lambda item: (
                _integer(item.get("height")) or 0,
                _integer(item.get("bitrate")) or 0,
            ),
            reverse=True,
        )
        return str(preferred[0]["url"])
    return str(media.get("url") or media.get("transcode_url") or "")


def _raw_media(tweet: dict) -> list[dict]:
    extended = tweet.get("media_extended")
    if isinstance(extended, list):
        return [item for item in extended if isinstance(item, dict)]
    media = tweet.get("media") or {}
    if isinstance(media, dict):
        combined = media.get("all")
        if isinstance(combined, list):
            return [item for item in combined if isinstance(item, dict)]
        return [
            item
            for group in (media.get("photos") or [], media.get("videos") or [])
            for item in group
            if isinstance(item, dict)
        ]
    return []


def normalize_tweet_payload(
    payload: dict,
    *,
    expected_id: str = "",
    source_url: str = "",
) -> MediaBundle:
    """Normaliza os formatos diferentes de VX e FX em um unico contrato."""
    tweet = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else payload
    if not isinstance(tweet, dict):
        raise ContentUnavailable("resposta do X nao contem tweet", platform="twitter", stage="normalize")
    raw_author = tweet.get("author")
    author_data: dict = raw_author if isinstance(raw_author, dict) else {}
    source_id = str(
        tweet.get("tweetID")
        or tweet.get("id")
        or tweet.get("id_str")
        or ""
    )
    canonical_url = str(tweet.get("tweetURL") or tweet.get("url") or source_url)
    if not source_id:
        match = re.search(r"/status/(\d+)", canonical_url)
        source_id = match.group(1) if match else ""
    if expected_id and source_id and source_id != expected_id:
        raise ContentUnavailable(
            "a API retornou outro tweet",
            platform="twitter",
            stage="identity",
        )

    raw_media = _raw_media(tweet)
    items: list[MediaItem] = []
    for index, media in enumerate(raw_media):
        raw_type = str(media.get("type") or "").lower()
        kind = "photo" if raw_type in {"photo", "image"} else "video"
        media_url = _best_media_url(media)
        if not media_url.startswith("https://"):
            continue
        items.append(
            MediaItem(
                media_url,
                kind,
                index=index,
                duration=_duration_seconds(media),
                width=_integer(media.get("width")),
                height=_integer(media.get("height")),
                source_id=str(media.get("id") or media.get("media_id") or ""),
                metadata={"alt_text": media.get("altText") or media.get("alt_text") or ""},
            )
        )

    quote_payload = tweet.get("qrt") or tweet.get("quote")
    quote_bundle = None
    if isinstance(quote_payload, dict):
        try:
            quote_bundle = normalize_tweet_payload(quote_payload)
        except ContentUnavailable:
            quote_bundle = None
    author = str(tweet.get("user_name") or author_data.get("name") or "Autor")
    screen_name = str(tweet.get("user_screen_name") or author_data.get("screen_name") or "")
    return MediaBundle(
        platform="twitter",
        items=tuple(items),
        title=str(tweet.get("text") or ""),
        author=author,
        text=str(tweet.get("text") or ""),
        source_url=canonical_url,
        source_id=source_id or expected_id,
        expected_items=len(raw_media),
        metadata={
            "screen_name": screen_name,
            "language": tweet.get("lang") or tweet.get("language"),
            "raw": tweet,
            "quote": quote_bundle,
            "quote_raw": quote_payload if isinstance(quote_payload, dict) else None,
        },
    )


class TwitterExtractor(SocialExtractor):
    def __init__(self, session: aiohttp.ClientSession, download_manager: DownloadManager) -> None:
        self.session = session
        self.download_manager = download_manager

    def supports(self, url: str) -> bool:
        return match_tweet_url(url) is not None

    async def _fetch(self, url: str) -> MediaBundle:
        match = match_tweet_url(url)
        if not match:
            raise UnsupportedUrl("URL do X nao contem status", platform="twitter")
        username, status_id = match.group(1), match.group(2)
        endpoints = (
            ("vxtwitter", build_vxtwitter_url(username, status_id)),
            ("fxtwitter", build_fxtwitter_url(username, status_id)),
        )
        errors: list[str] = []
        for provider, endpoint in endpoints:
            try:
                async with self.session.get(
                    endpoint,
                    headers={"Accept-Encoding": "gzip, deflate"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 429:
                        raise RateLimited(
                            f"{provider} limitou requisicoes",
                            platform="twitter",
                            stage=provider,
                            status_code=429,
                        )
                    if response.status != 200:
                        errors.append(f"{provider}=HTTP {response.status}")
                        continue
                    payload = await response.json(content_type=None)
                    return normalize_tweet_payload(
                        payload,
                        expected_id=status_id,
                        source_url=url,
                    )
            except RateLimited as exc:
                errors.append(str(exc))
            except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as exc:
                errors.append(f"{provider}={type(exc).__name__}")
        raise ContentUnavailable(
            "APIs do X indisponiveis: " + ", ".join(errors),
            platform="twitter",
            stage="metadata",
        )

    async def extract(
        self,
        url: str,
        *,
        context: ExtractionContext | None = None,
    ) -> MediaBundle:
        try:
            return await self._fetch(url)
        except ContentUnavailable:
            # Uma unica chamada baixa todas as entradas do tweet; nunca uma
            # chamada por video, evitando repetir sempre a primeira entrada.
            options = {
                "platform": "twitter",
                "allow_playlist": True,
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
            return await self.download_manager.download(url, **options)
