"""Filename normalization required by Telegram media uploads."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


async def ensure_photo_upload_extension(
    path: Path,
    mime: str,
    directory: Path,
    index: int,
) -> Path:
    """Copy a valid photo to a filename accepted by Telegram.

    Instagram CDN URLs can be extensionless.  They download as ``.bin`` even
    when their bytes are valid JPEGs or PNGs, which Telegram rejects with
    ``PHOTO_EXT_INVALID``.
    """
    extensions = {
        "image/jpeg": {".jpg", ".jpeg"},
        "image/png": {".png"},
    }
    valid_suffixes = extensions.get(mime, set())
    if path.suffix.lower() in valid_suffixes:
        return path
    extension = ".jpg" if mime == "image/jpeg" else ".png"
    destination = directory / f"photo_{index:03d}{extension}"
    await asyncio.to_thread(shutil.copyfile, path, destination)
    return destination
