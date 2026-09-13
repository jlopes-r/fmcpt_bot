"""Orquestracao do X fora do entrypoint principal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from apps.telegram_bot.errors import SocialMediaError
from apps.telegram_bot.extractors.base import ExtractionContext
from apps.telegram_bot.extractors.twitter import TwitterExtractor
from apps.telegram_bot.models.media import MediaBundle
from apps.telegram_bot.services.media_sender import MediaSender
from apps.telegram_bot.text_utils import dividir_texto_longo, montar_legenda
from apps.telegram_bot.translator import traduzir_se_necessario
from apps.telegram_bot.twitter import traduzir_texto_tweet


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
) -> TwitterDelivery:
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
    if bundle.items:
        caption = montar_legenda(
            main_text,
            bundle.author or "Autor",
            requested_by,
            emoji="📸",
        )
        prepared = await sender.send(message, bundle, caption=caption, status=status)
        main_count = len(prepared.items)
    else:
        text_message = (
            f"📝 {bundle.author or 'Autor'}:\n{main_text}\n\n"
            f"👤 Enviado por: {requested_by}"
        )
        for part in dividir_texto_longo(text_message):
            await message.reply_text(part)

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
            prepared_quote = await sender.send(
                message,
                quote,
                caption=quote_caption,
                status=status,
            )
            quote_count = len(prepared_quote.items)

    try:
        await status.delete()
    except Exception:
        pass
    return TwitterDelivery(
        main_count,
        quote_count,
        not bundle.items,
        bundle,
        quote_bundle=quote,
    )
