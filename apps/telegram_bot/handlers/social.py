"""Orquestracao unica dos extratores sociais e do envio ao Telegram."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

from apps.telegram_bot.extractors.base import ExtractionContext
from apps.telegram_bot.extractors.registry import ExtractorRegistry, build_default_registry
from apps.telegram_bot.extractors.twitter import TwitterExtractor
from apps.telegram_bot.handlers.twitter import deliver_twitter_post
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.download_manager import DownloadManager
from apps.telegram_bot.services.media_sender import MediaSender, ProgressCallback
from apps.telegram_bot.text_utils import dividir_texto_longo, limpar_texto, montar_legenda
from apps.telegram_bot.translator import traduzir_se_necessario


log = logging.getLogger(__name__)
LongVideoCallback = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class SocialPipelineConfig:
    download_root: Path
    max_media_bytes: int
    download_timeout: float
    duration_limit: float
    media_download_concurrency: int = 3
    playlist_limit: int = 20
    instagram_cookie_path: str = ""
    instagram_secondary_cookie_path: str = ""


@dataclass(frozen=True)
class SocialDelivery:
    platform: str
    item_count: int
    text_only: bool
    partial: bool
    bundle: MediaBundle
    skipped: bool = False


class SocialMediaPipeline:
    """Resolve, extrai, prepara e envia qualquer rede registrada."""

    def __init__(
        self,
        *,
        client,
        session: aiohttp.ClientSession,
        config: SocialPipelineConfig,
        progress: ProgressCallback | None = None,
        registry: ExtractorRegistry | None = None,
    ) -> None:
        self.client = client
        self.session = session
        self.config = config
        self.download_manager = DownloadManager(
            config.download_root,
            max_filesize=config.max_media_bytes,
            timeout=config.download_timeout,
        )
        self.registry = registry or build_default_registry(
            session,
            self.download_manager,
            instagram_cookie_path=config.instagram_cookie_path,
            instagram_secondary_cookie_path=config.instagram_secondary_cookie_path,
        )
        self.sender = MediaSender(
            client,
            session=session,
            max_media_bytes=config.max_media_bytes,
            download_concurrency=config.media_download_concurrency,
            progress=progress,
        )

    def context(
        self,
        *,
        status=None,
        cancel_event=None,
        reply_markup=None,
        force_long: bool = False,
    ) -> ExtractionContext:
        return ExtractionContext(
            duration_limit=None if force_long else self.config.duration_limit,
            status=status,
            cancel_event=cancel_event,
            reply_markup=reply_markup,
            playlist_limit=self.config.playlist_limit,
        )

    def _cleanup_local_sources(self, bundle: MediaBundle) -> None:
        root = self.config.download_root.resolve()
        for item in bundle.items:
            if item.is_remote:
                continue
            try:
                source = Path(item.source).resolve()
                source.relative_to(root)
                source.unlink(missing_ok=True)
            except ValueError:
                log.debug("Fonte local externa preservada: %s", item.source)
            except OSError as exc:
                log.warning("Falha ao limpar %s: %s", item.source, type(exc).__name__)

    async def _send_bundle(
        self,
        *,
        message,
        bundle: MediaBundle,
        requested_by: str,
        status,
    ) -> int:
        text = limpar_texto(bundle.text or bundle.title)
        text = await asyncio.to_thread(traduzir_se_necessario, text)
        if bundle.items:
            caption = montar_legenda(
                text,
                bundle.author or "Autor",
                requested_by,
                emoji="📸" if bundle.platform == "instagram" else "✨",
            )
            prepared = await self.sender.send(
                message,
                bundle,
                caption=caption,
                status=status,
            )
            return len(prepared.items)
        if text:
            for part in dividir_texto_longo(text):
                await message.reply_text(part, parse_mode=None)
        return 0

    async def deliver(
        self,
        *,
        message,
        url: str,
        requested_by: str,
        status,
        cancel_event=None,
        reply_markup=None,
        force_long: bool = False,
        long_video_callback: LongVideoCallback,
    ) -> SocialDelivery:
        extractor = self.registry.resolve(url)
        context = self.context(
            status=status,
            cancel_event=cancel_event,
            reply_markup=reply_markup,
            force_long=force_long,
        )

        if isinstance(extractor, TwitterExtractor):
            outcome = await deliver_twitter_post(
                client=self.client,
                message=message,
                url=url,
                requested_by=requested_by,
                status=status,
                extractor=extractor,
                sender=self.sender,
                duration_limit=(
                    float("inf") if force_long else self.config.duration_limit
                ),
                long_video_callback=long_video_callback,
                extraction_context=context,
            )
            try:
                return SocialDelivery(
                    platform="twitter",
                    item_count=outcome.main_items + outcome.quote_items,
                    text_only=outcome.text_only,
                    partial=outcome.bundle.is_partial,
                    bundle=outcome.bundle,
                    skipped=outcome.skipped,
                )
            finally:
                self._cleanup_local_sources(outcome.bundle)
                if outcome.quote_bundle is not None:
                    self._cleanup_local_sources(outcome.quote_bundle)

        bundle = await extractor.extract(url, context=context)
        try:
            if not force_long and any(
                (item.duration or 0) > self.config.duration_limit
                for item in bundle.items
            ):
                await long_video_callback(status, url, requested_by, message)
                return SocialDelivery(
                    platform=bundle.platform,
                    item_count=0,
                    text_only=False,
                    partial=bundle.is_partial,
                    bundle=bundle,
                    skipped=True,
                )
            item_count = await self._send_bundle(
                message=message,
                bundle=bundle,
                requested_by=requested_by,
                status=status,
            )
            text_only = not bundle.items and bool(bundle.text or bundle.title)
            if item_count or text_only:
                try:
                    await status.delete()
                except Exception:
                    pass
            return SocialDelivery(
                platform=bundle.platform,
                item_count=item_count,
                text_only=text_only,
                partial=bundle.is_partial,
                bundle=bundle,
            )
        finally:
            self._cleanup_local_sources(bundle)


__all__ = ["SocialDelivery", "SocialMediaPipeline", "SocialPipelineConfig"]
