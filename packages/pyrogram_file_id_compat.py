"""Compatibilidade com o formato de file_id de foto emitido pelo Telegram.

O Telegram mudou o layout dos file_ids de foto (minor version >= 61): o final
da estrutura deixou de incluir volume_id e local_id, ficando apenas
thumbnail_source + thumbnail_file_type + thumbnail_size. O Pyrogram 2.0.106
não decodifica esse formato e falha com "Unknown thumbnail_source".

Este módulo aplica um patch em ``pyrogram.file_id.FileId.decode`` para
reconhecer o formato novo. Para envio, apenas media_id, access_hash e
file_reference importam, então os campos extras são preenchidos com zeros.
"""
from __future__ import annotations

import struct
from io import BytesIO

import pyrogram.file_id as _file_id

from pyrogram.file_id import (
    FileId,
    FileType,
    ThumbnailSource,
    b64_decode,
    rle_decode,
)
from pyrogram.raw.core import Bytes, String

_PATCHED = False
_ORIGINAL_DECODE = _file_id.FileId.decode


def _decode_new_photo(file_id: str) -> FileId:
    decoded = rle_decode(b64_decode(file_id))
    major = decoded[-1]
    if major < 4:
        minor = 0
        buffer = BytesIO(decoded[:-1])
    else:
        minor = decoded[-2]
        buffer = BytesIO(decoded[:-2])

    file_type, dc_id = struct.unpack("<ii", buffer.read(8))

    has_web_location = bool(file_type & _file_id.WEB_LOCATION_FLAG)
    has_file_reference = bool(file_type & _file_id.FILE_REFERENCE_FLAG)

    file_type &= ~_file_id.WEB_LOCATION_FLAG
    file_type &= ~_file_id.FILE_REFERENCE_FLAG

    file_type = FileType(file_type)

    if has_web_location:
        url = String.read(buffer)
        access_hash, = struct.unpack("<q", buffer.read(8))
        return FileId(
            major=major,
            minor=minor,
            file_type=file_type,
            dc_id=dc_id,
            url=url,
            access_hash=access_hash,
        )

    file_reference = Bytes.read(buffer) if has_file_reference else b""
    media_id, access_hash = struct.unpack("<qq", buffer.read(16))

    thumbnail_source, thumbnail_file_type, thumbnail_size = struct.unpack(
        "<iii", buffer.read(12)
    )

    return FileId(
        major=major,
        minor=minor,
        file_type=file_type,
        dc_id=dc_id,
        file_reference=file_reference,
        media_id=media_id,
        access_hash=access_hash,
        volume_id=0,
        thumbnail_source=ThumbnailSource(thumbnail_source),
        thumbnail_file_type=FileType(thumbnail_file_type),
        thumbnail_size=chr(thumbnail_size),
        local_id=0,
    )


def _decode_compat(file_id: str) -> FileId:
    try:
        return _ORIGINAL_DECODE(file_id)
    except ValueError:
        try:
            return _decode_new_photo(file_id)
        except Exception:
            raise ValueError(
                f'Failed to decode "{file_id}". The value does not represent an '
                "existing local file, HTTP URL, or valid file id."
            )


def patch_pyrogram_file_id() -> None:
    """Substitui ``FileId.decode`` por uma versão que aceita os dois formatos."""
    global _PATCHED
    if _PATCHED:
        return
    _file_id.FileId.decode = staticmethod(_decode_compat)
    _PATCHED = True
