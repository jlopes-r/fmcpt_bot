"""Preparacao e envio centralizado de fotos/videos ao Telegram."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, TypedDict
from urllib.parse import urlparse

import aiohttp
from PIL import Image
from pyrogram.types import InputMediaPhoto, InputMediaVideo

from apps.telegram_bot.downloaders import baixar_url_limitado
from apps.telegram_bot.errors import (
    ContentUnavailable,
    MediaTooLarge,
    TelegramUploadFailed,
)
from apps.telegram_bot.models.media import MediaBundle, MediaItem


log = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int], Awaitable[None]]


class VideoProbe(TypedDict):
    width: int
    height: int
    duration: int
    video_codec: str
    audio_codec: str
    format: str


def sniff_media(path: Path) -> tuple[str, str]:
    """Retorna ``(kind, mime)`` usando assinatura do arquivo, nao so extensao."""
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError as exc:
        raise ContentUnavailable(f"arquivo de midia ilegivel: {path.name}", stage="validate") from exc
    if len(head) < 4:
        raise ContentUnavailable(f"arquivo de midia vazio: {path.name}", stage="validate")
    if head.startswith(b"\xff\xd8\xff"):
        return "photo", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "photo", "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "photo", "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "photo", "image/webp"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video", "video/webm"
    if b"ftyp" in head[:32]:
        brand = head[8:16]
        if any(marker in brand for marker in (b"heic", b"heix", b"hevc", b"mif1")):
            return "photo", "image/heic"
        return "video", "video/mp4"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("audio/"):
        return "audio", guessed
    raise ContentUnavailable(
        f"assinatura de midia nao reconhecida: {path.name}", stage="validate"
    )


def probe_video(path: Path, timeout: float = 15.0) -> VideoProbe:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,duration:format=format_name,duration",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise ContentUnavailable("ffprobe rejeitou o video", stage="probe")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContentUnavailable("resposta invalida do ffprobe", stage="probe") from exc
    streams = payload.get("streams") or []
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if not video:
        raise ContentUnavailable("arquivo nao contem faixa de video", stage="probe")
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    duration = video.get("duration") or (payload.get("format") or {}).get("duration") or 0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "duration": max(0, int(float(duration or 0))),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str((audio or {}).get("codec_name") or ""),
        "format": str((payload.get("format") or {}).get("format_name") or ""),
    }


class MediaSender:
    def __init__(
        self,
        client,
        *,
        session: aiohttp.ClientSession | None = None,
        max_media_bytes: int = 2_000_000_000,
        download_concurrency: int = 3,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.client = client
        self.session = session
        self.max_media_bytes = max_media_bytes
        self.download_concurrency = max(1, download_concurrency)
        self.progress = progress

    async def _download_remote(self, item: MediaItem, directory: Path) -> Path:
        if self.session is None:
            raise ContentUnavailable("sessao HTTP ausente para midia remota", stage="download")
        parsed = urlparse(item.source)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ContentUnavailable("URL de midia remota insegura", stage="download")
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".mp4", ".mov", ".m4v", ".webm"}:
            suffix = ".bin"
        destination = directory / f"remote_{item.index:03d}{suffix}"
        try:
            await baixar_url_limitado(
                self.session,
                item.source,
                str(destination),
                self.max_media_bytes,
            )
        except ValueError as exc:
            raise MediaTooLarge(
                "midia remota excede o limite", stage="download"
            ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ContentUnavailable("falha ao baixar midia remota", stage="download") from exc
        return destination

    async def _convert_photo(self, path: Path, directory: Path, index: int) -> Path:
        destination = directory / f"photo_{index:03d}.jpg"

        def convert() -> None:
            try:
                with Image.open(path) as image:
                    image.convert("RGB").save(destination, "JPEG", quality=94, optimize=True)
                return
            except Exception:
                pass
            completed = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(path), str(destination)],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if completed.returncode or not destination.is_file():
                raise ContentUnavailable("nao foi possivel converter a imagem", stage="convert")

        await asyncio.to_thread(convert)
        return destination

    async def _transcode_video(self, path: Path, directory: Path, index: int) -> Path:
        destination = directory / f"video_{index:03d}.mp4"

        def transcode() -> None:
            completed = subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(path),
                    "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", str(destination),
                ],
                capture_output=True,
                timeout=1800,
                check=False,
            )
            if completed.returncode or not destination.is_file():
                raise ContentUnavailable("nao foi possivel transcodificar o video", stage="convert")

        await asyncio.to_thread(transcode)
        if destination.stat().st_size > self.max_media_bytes:
            raise MediaTooLarge("video convertido excede o limite", stage="convert")
        return destination

    async def _prepare_item(self, item: MediaItem, directory: Path) -> MediaItem:
        path = await self._download_remote(item, directory) if item.is_remote else Path(item.source)
        if not path.is_file():
            raise ContentUnavailable("arquivo de midia nao existe", stage="validate")
        size = path.stat().st_size
        if size > self.max_media_bytes:
            raise MediaTooLarge("midia excede o limite", stage="validate")
        detected_kind, mime = await asyncio.to_thread(sniff_media, path)

        if item.kind == "photo" and detected_kind != "photo":
            raise ContentUnavailable("esperava foto, mas o arquivo e outro tipo", stage="validate")
        if item.kind == "video" and detected_kind != "video":
            raise ContentUnavailable("esperava video, mas o arquivo e outro tipo", stage="validate")

        if detected_kind == "photo":
            if mime not in {"image/jpeg", "image/png"}:
                path = await self._convert_photo(path, directory, item.index)
                mime = "image/jpeg"
            return item.with_source(str(path), mime_type=mime, size=path.stat().st_size)

        if detected_kind == "video":
            probe = await asyncio.to_thread(probe_video, path)
            compatible = (
                probe["video_codec"] == "h264"
                and probe["audio_codec"] in {"", "aac", "mp3"}
                and "mp4" in str(probe["format"])
            )
            if not compatible:
                path = await self._transcode_video(path, directory, item.index)
                probe = await asyncio.to_thread(probe_video, path)
            return item.with_source(
                str(path),
                mime_type="video/mp4",
                size=path.stat().st_size,
                width=int(probe["width"]),
                height=int(probe["height"]),
                duration=float(probe["duration"]),
            )
        raise ContentUnavailable("tipo de midia ainda nao suportado", stage="validate")

    async def prepare(self, bundle: MediaBundle, directory: Path) -> MediaBundle:
        semaphore = asyncio.Semaphore(self.download_concurrency)

        async def prepare_one(item: MediaItem) -> MediaItem | None:
            async with semaphore:
                try:
                    return await self._prepare_item(item, directory)
                except (ContentUnavailable, MediaTooLarge) as exc:
                    log.warning(
                        "midia ignorada platform=%s index=%d stage=%s error=%s",
                        bundle.platform,
                        item.index,
                        exc.stage,
                        exc,
                    )
                    return None

        prepared = await asyncio.gather(*(prepare_one(item) for item in bundle.items))
        valid = tuple(item for item in prepared if item is not None)
        if not valid:
            raise ContentUnavailable(
                "nenhuma midia valida depois da preparacao",
                platform=bundle.platform,
                stage="prepare",
            )
        return MediaBundle(
            platform=bundle.platform,
            items=valid,
            title=bundle.title,
            author=bundle.author,
            text=bundle.text,
            source_url=bundle.source_url,
            source_id=bundle.source_id,
            expected_items=bundle.expected_items,
            metadata=bundle.metadata,
        )

    @staticmethod
    def _input_media(item: MediaItem, caption: str):
        if item.kind == "photo":
            return InputMediaPhoto(item.source, caption=caption)
        if item.kind == "video":
            return InputMediaVideo(
                item.source,
                caption=caption,
                width=item.width or 0,
                height=item.height or 0,
                duration=int(item.duration or 0),
                supports_streaming=True,
            )
        raise ContentUnavailable("tipo nao aceito em album", stage="upload")

    async def _send_one(self, chat_id: int, reply_to: int, item, status=None) -> None:
        try:
            if isinstance(item, InputMediaPhoto):
                await self.client.send_photo(
                    chat_id,
                    item.media,
                    caption=item.caption,
                    reply_to_message_id=reply_to,
                )
            else:
                await self.client.send_video(
                    chat_id,
                    item.media,
                    caption=item.caption,
                    width=item.width,
                    height=item.height,
                    duration=item.duration,
                    supports_streaming=True,
                    reply_to_message_id=reply_to,
                    progress=self.progress,
                )
        except Exception as exc:
            raise TelegramUploadFailed("Telegram rejeitou a midia", stage="upload") from exc

    async def _send_batch(self, chat_id: int, reply_to: int, batch: list, status=None) -> None:
        if len(batch) == 1:
            await self._send_one(chat_id, reply_to, batch[0], status)
            return
        try:
            await self.client.send_media_group(
                chat_id,
                batch,
                reply_to_message_id=reply_to,
            )
        except Exception as exc:
            log.warning("album rejeitado; enviando itens individualmente: %s", type(exc).__name__)
            for item in batch:
                await self._send_one(chat_id, reply_to, item, status)

    async def send(self, message, bundle: MediaBundle, *, caption: str, status=None) -> MediaBundle:
        root = Path(tempfile.mkdtemp(prefix="media-send-"))
        try:
            prepared = await self.prepare(bundle.require_media(), root)
            telegram_items = [
                self._input_media(item, caption if index == 0 else "")
                for index, item in enumerate(prepared.items)
            ]
            for offset in range(0, len(telegram_items), 10):
                batch = telegram_items[offset:offset + 10]
                if status is not None:
                    try:
                        await status.edit_text(
                            f"📤 Enviando {offset + 1}-{offset + len(batch)} "
                            f"de {len(telegram_items)}..."
                        )
                    except Exception:
                        pass
                await self._send_batch(message.chat.id, message.id, batch, status)
            return prepared
        finally:
            shutil.rmtree(root, ignore_errors=True)
