"""Orquestracao do X fora do entrypoint principal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import re
from urllib.parse import urlsplit

from pyrogram import enums

from apps.telegram_bot.errors import SocialMediaError, TelegramUploadFailed
from apps.telegram_bot.extractors.base import ExtractionContext
from apps.telegram_bot.extractors.twitter import TwitterExtractor
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.media_sender import MediaSender
from apps.telegram_bot.services.observability import PipelineObserver
from apps.telegram_bot.text_utils import dividir_texto_longo, montar_legenda
from apps.telegram_bot.translator import traduzir_se_necessario
from apps.telegram_bot.twitter import traduzir_texto_tweet


log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>]+")
_TWITTER_HOSTS = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}


@dataclass(frozen=True)
class TwitterDelivery:
    main_items: int
    quote_items: int
    text_only: bool
    bundle: MediaBundle
    skipped: bool = False
    quote_bundle: MediaBundle | None = None


def _translated_text(bundle: MediaBundle) -> str:
    raw = bundle.metadata.get("raw")
    if isinstance(raw, dict):
        return traduzir_texto_tweet(raw)
    return traduzir_se_necessario(bundle.text or bundle.title)


def _is_external_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and (parsed.hostname or "").lower() not in _TWITTER_HOSTS
    )


def _article_url(bundle: MediaBundle, text: str) -> str:
    raw = bundle.metadata.get("raw")
    if isinstance(raw, dict):
        card = raw.get("card")
        if isinstance(card, dict) and _is_external_http_url(card.get("url")):
            return str(card["url"])

    for candidate in _URL_RE.findall(text):
        if _is_external_http_url(candidate):
            return candidate
    return ""


@asynccontextmanager
async def _observe_stage(
    observer: PipelineObserver | None,
    stage: str,
) -> AsyncIterator[None]:
    if observer is None:
        yield
        return
    async with observer.async_stage(stage, platform="twitter"):
        yield


async def _send_text_fallback(
    message,
    bundle: MediaBundle,
    text: str,
    requested_by: str,
    *,
    reason: str,
) -> None:
    article_url = _article_url(bundle, text)
    displayed_text = text.replace(article_url, "").strip() if article_url else text

    sections = [
        f"📝 {reason}",
        (
            f"{bundle.author or 'Autor'}:\n{displayed_text}"
            if displayed_text
            else bundle.author or "Autor"
        ),
    ]
    fallback_url = article_url or bundle.source_url
    if fallback_url:
        sections.append(f"🔗 {fallback_url}")
    sections.append(f"👤 Enviado por: {requested_by}")
    for part in dividir_texto_longo("\n\n".join(sections)):
        await message.reply_text(part, parse_mode=enums.ParseMode.DISABLED)


async def _complete_quote(
    extractor: TwitterExtractor,
    bundle: MediaBundle,
    context: ExtractionContext | None = None,
) -> MediaBundle | None:
    quote = bundle.metadata.get("quote")
    if not isinstance(quote, MediaBundle):
        return None
    if quote.items:
        return quote
    raw = bundle.metadata.get("quote_raw")
    if not isinstance(raw, dict):
        return quote
    quote_id = str(raw.get("id") or raw.get("tweetID") or "")
    raw_author = raw.get("author")
    author: dict = raw_author if isinstance(raw_author, dict) else {}
    username = str(raw.get("user_screen_name") or author.get("screen_name") or "")
    quote_url = str(raw.get("tweetURL") or raw.get("url") or "")
    if not quote_url and quote_id:
        quote_url = f"https://x.com/{username or 'i'}/status/{quote_id}"
    if not quote_url:
        return quote
    try:
        if context is None:
            return await extractor.extract(quote_url)
        return await extractor.extract(quote_url, context=context)
    except SocialMediaError:
        return quote


async def deliver_twitter_post(
    *,
    client,
    message,
    url: str,
    requested_by: str,
    status,
    extractor: TwitterExtractor,
    sender: MediaSender,
    duration_limit: float,
    long_video_callback,
    extraction_context: ExtractionContext | None = None,
    upload_started: Callable[[], Awaitable[None]] | None = None,
    observer: PipelineObserver | None = None,
) -> TwitterDelivery:
    async with _observe_stage(observer, "extract"):
        if extraction_context is None:
            bundle = await extractor.extract(url)
        else:
            bundle = await extractor.extract(url, context=extraction_context)
        quote = await _complete_quote(extractor, bundle, extraction_context)
    main_text = await asyncio.to_thread(_translated_text, bundle)

    if any((item.duration or 0) > duration_limit for item in bundle.items):
        await long_video_callback(status, url, requested_by, message)
        return TwitterDelivery(0, 0, False, bundle, skipped=True, quote_bundle=quote)

    quote_has_media = bool(quote and quote.items)
    if quote:
        if quote_has_media:
            main_text += f"\n\n🔁 [Quote de {quote.author or 'Autor'} logo abaixo 👇]"
        elif quote.text or quote.title:
            quote_text = await asyncio.to_thread(_translated_text, quote)
            main_text += f"\n\n🔁 [Quote - {quote.author or 'Autor'}]:\n{quote_text}"

    main_count = 0
    main_text_fallback = False
    if bundle.items:
        caption = montar_legenda(
            main_text,
            bundle.author or "Autor",
            requested_by,
            emoji="📸",
        )
        try:
            prepared = await sender.send(
                message,
                bundle,
                caption=caption,
                status=status,
                upload_started=upload_started,
            )
            main_count = len(prepared.items)
        except TelegramUploadFailed as exc:
            cause = exc.__cause__ or exc
            log.warning(
                "midia principal do X rejeitada; usando fallback "
                "source_id=%s cause_type=%s",
                bundle.source_id,
                type(cause).__name__,
            )
            if observer is not None:
                await asyncio.to_thread(
                    observer.record_fallback,
                    "text_link",
                    platform="twitter",
                    stage="upload",
                )
            async with _observe_stage(observer, "fallback"):
                await _send_text_fallback(
                    message,
                    bundle,
                    main_text,
                    requested_by,
                    reason="Prévia indisponível; conteúdo enviado em texto",
                )
            main_text_fallback = True
    else:
        async with _observe_stage(observer, "upload"):
            if upload_started is not None:
                await upload_started()
            text_message = (
                f"📝 {bundle.author or 'Autor'}:\n{main_text}\n\n"
                f"👤 Enviado por: {requested_by}"
            )
            for part in dividir_texto_longo(text_message):
                await message.reply_text(part, parse_mode=enums.ParseMode.DISABLED)

    quote_count = 0
    if quote_has_media and quote is not None:
        if any((item.duration or 0) > duration_limit for item in quote.items):
            await message.reply_text("⏱️ A mídia da citação excede o limite configurado.")
        else:
            quote_text = await asyncio.to_thread(_translated_text, quote)
            quote_caption = montar_legenda(
                f"📎 Mídia do quote\n{quote_text}" if quote_text else "📎 Mídia do quote",
                quote.author or "Autor",
                requested_by,
                emoji="🔁",
            )
            try:
                prepared_quote = await sender.send(
                    message,
                    quote,
                    caption=quote_caption,
                    status=status,
                    upload_started=upload_started,
                )
                quote_count = len(prepared_quote.items)
            except TelegramUploadFailed as exc:
                cause = exc.__cause__ or exc
                log.warning(
                    "midia citada do X rejeitada; usando fallback "
                    "source_id=%s cause_type=%s",
                    quote.source_id,
                    type(cause).__name__,
                )
                if observer is not None:
                    await asyncio.to_thread(
                        observer.record_fallback,
                        "quoted_text",
                        platform="twitter",
                        stage="upload",
                    )
                async with _observe_stage(observer, "fallback"):
                    await _send_text_fallback(
                        message,
                        quote,
                        quote_text,
                        requested_by,
                        reason="Mídia citada indisponível; conteúdo enviado em texto",
                    )

    try:
        await status.delete()
    except Exception:
        pass
    return TwitterDelivery(
        main_count,
        quote_count,
        not bundle.items or main_text_fallback,
        bundle,
        quote_bundle=quote,
    )
