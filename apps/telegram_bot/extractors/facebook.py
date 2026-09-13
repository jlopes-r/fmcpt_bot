"""Typed Facebook extractor with explicit yt-dlp/public-HTML fallbacks."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import aiohttp

from apps.telegram_bot.errors import (
    AuthenticationRequired,
    ContentUnavailable,
    SocialMediaError,
    UnsupportedUrl,
)
from apps.telegram_bot.extractors.base import ExtractionContext, SocialExtractor
from apps.telegram_bot.facebook import (
    FacebookAccessRestricted,
    FacebookTarget,
    fetch_public_post,
    parse_facebook_target,
)
from apps.telegram_bot.models.media import MediaBundle, MediaItem
from apps.telegram_bot.services.download_manager import DownloadManager


def public_post_to_bundle(
    post: dict[str, Any],
    *,
    source_url: str,
    target: FacebookTarget,
) -> MediaBundle:
    """Convert the backwards-compatible HTML result to the common contract."""
    raw_media = post.get("media") or []
    if not raw_media:
        raw_media = [
            *({"type": "photo", "url": url, "id": ""} for url in post.get("photos") or []),
            *({"type": "video", "url": url, "id": ""} for url in post.get("videos") or []),
        ]
    items: list[MediaItem] = []
    layout: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_media):
        if not isinstance(raw, dict):
            continue
        kind = "video" if str(raw.get("type") or "").lower() == "video" else "photo"
        media_url = str(raw.get("url") or "")
        layout.append({"index": index, "kind": kind, "url": media_url})
        if media_url:
            items.append(
                MediaItem(
                    media_url,
                    kind,
                    index=index,
                    source_id=str(raw.get("id") or ""),
                )
            )
    expected = post.get("expected_items")
    if not isinstance(expected, int):
        expected = len(layout)
    return MediaBundle(
        platform="facebook",
        items=tuple(items),
        title=str(post.get("text") or ""),
        text=str(post.get("text") or ""),
        author=str(post.get("author") or "Facebook"),
        source_url=str(post.get("resolved_url") or source_url),
        source_id=str(post.get("source_id") or target.content_id),
        expected_items=expected,
        metadata={
            "content_type": str(post.get("content_type") or target.kind),
            "has_video": bool(post.get("has_video")),
            "media_layout": tuple(layout),
            "html": post,
        },
    )


def _annotate(bundle: MediaBundle, strategy: str, **metadata: Any) -> MediaBundle:
    return replace(
        bundle,
        metadata={**bundle.metadata, "extraction_strategy": strategy, **metadata},
    )


def _merge_html_and_downloaded(
    html_bundle: MediaBundle,
    downloaded: MediaBundle,
) -> MediaBundle:
    """Use HTML layout while preferring locally downloaded media for each slot."""
    queues: dict[str, list[MediaItem]] = {"photo": [], "video": []}
    for item in downloaded.items:
        if item.kind in queues:
            queues[item.kind].append(item)
    html_by_index = {item.index: item for item in html_bundle.items}
    merged: list[MediaItem] = []
    used_sources: set[str] = set()
    layout = html_bundle.metadata.get("media_layout") or ()
    for raw in layout:
        if not isinstance(raw, dict):
            continue
        index = int(raw.get("index") or 0)
        kind = "video" if raw.get("kind") == "video" else "photo"
        item = queues[kind].pop(0) if queues[kind] else html_by_index.get(index)
        if item is not None and item.source not in used_sources:
            merged.append(replace(item, index=index))
            used_sources.add(item.source)
    next_index = len(layout)
    for item in downloaded.items:
        if item.source not in used_sources:
            merged.append(replace(item, index=next_index))
            used_sources.add(item.source)
            next_index += 1
    if not merged:
        merged = list(html_bundle.items or downloaded.items)
    expected_values = [
        value
        for value in (html_bundle.expected_items, downloaded.expected_items)
        if value is not None
    ]
    return MediaBundle(
        platform="facebook",
        items=tuple(merged),
        title=html_bundle.title or downloaded.title,
        text=html_bundle.text or downloaded.text,
        author=html_bundle.author or downloaded.author,
        source_url=html_bundle.source_url or downloaded.source_url,
        source_id=html_bundle.source_id or downloaded.source_id,
        expected_items=max(expected_values) if expected_values else len(merged),
        metadata={
            **downloaded.metadata,
            **html_bundle.metadata,
            "extraction_strategy": "html+yt-dlp",
        },
    )


class FacebookExtractor(SocialExtractor):
    platform = "facebook"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        download_manager: DownloadManager,
        *,
        duration_limit: float | None = None,
    ) -> None:
        self.session = session
        self.download_manager = download_manager
        self.duration_limit = duration_limit

    def supports(self, url: str) -> bool:
        target = parse_facebook_target(url)
        return target is not None and target.kind != "unknown"

    async def _extract_html(self, url: str, target: FacebookTarget) -> MediaBundle:
        try:
            post = await fetch_public_post(self.session, url, target.content_id)
        except FacebookAccessRestricted as exc:
            raise AuthenticationRequired(
                "Facebook exige login para este conteudo",
                platform="facebook",
                stage="html",
            ) from exc
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as exc:
            raise ContentUnavailable(
                "HTML publico do Facebook indisponivel",
                platform="facebook",
                stage="html",
            ) from exc
        if not post:
            raise ContentUnavailable(
                "HTML nao contem a publicacao solicitada",
                platform="facebook",
                stage="identity",
            )
        return public_post_to_bundle(post, source_url=url, target=target)

    async def _extract_ytdlp(
        self,
        url: str,
        target: FacebookTarget,
        context: ExtractionContext | None = None,
    ) -> MediaBundle:
        options = {
            "platform": "facebook",
            "allow_playlist": target.kind in {"post", "story"},
            "playlist_limit": 20,
        }
        duration_limit = context.duration_limit if context else self.duration_limit
        if duration_limit is not None:
            options["duration_limit"] = duration_limit
        if context is not None:
            options.update(
                status=context.status,
                cancel_event=context.cancel_event,
                reply_markup=context.reply_markup,
                playlist_limit=context.playlist_limit,
            )
        return await self.download_manager.download(url, **options)

    async def extract(
        self,
        url: str,
        *,
        context: ExtractionContext | None = None,
    ) -> MediaBundle:
        target = parse_facebook_target(url)
        if target is None or target.kind == "unknown":
            raise UnsupportedUrl("URL do Facebook sem conteudo reconhecido", platform="facebook")

        html_bundle: MediaBundle | None = None
        html_error: SocialMediaError | None = None
        download_error: SocialMediaError | None = None

        # Feed posts are parsed as HTML first so photo carousels and text are
        # preserved. Video-like URLs use yt-dlp first because playable URLs in
        # Facebook HTML are short-lived and frequently incomplete.
        if target.kind == "post":
            try:
                html_bundle = await self._extract_html(url, target)
                if not html_bundle.metadata.get("has_video"):
                    return _annotate(html_bundle, "public-html")
            except SocialMediaError as exc:
                html_error = exc

        try:
            downloaded = await self._extract_ytdlp(url, target, context)
            if html_bundle is not None:
                return _merge_html_and_downloaded(html_bundle, downloaded)
            return _annotate(downloaded, "yt-dlp")
        except SocialMediaError as exc:
            download_error = exc

        if html_bundle is None:
            try:
                html_bundle = await self._extract_html(url, target)
            except SocialMediaError as exc:
                html_error = exc

        if html_bundle is not None and (html_bundle.items or html_bundle.text):
            return _annotate(
                html_bundle,
                "public-html-fallback",
                fallback_error=str(download_error or ""),
            )
        if isinstance(html_error, AuthenticationRequired):
            raise html_error
        if download_error is not None:
            raise download_error
        if html_error is not None:
            raise html_error
        raise ContentUnavailable("Facebook nao retornou conteudo", platform="facebook")


__all__ = ["FacebookExtractor", "public_post_to_bundle"]
