"""Modelos comuns usados por todos os extratores e pelo envio ao Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse


MediaKind = Literal["photo", "video", "audio", "document"]


@dataclass(frozen=True)
class MediaItem:
    source: str
    kind: MediaKind
    index: int = 0
    mime_type: str = ""
    size: int | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise ValueError("MediaItem.source nao pode ser vazio")
        if self.kind not in {"photo", "video", "audio", "document"}:
            raise ValueError(f"Tipo de midia invalido: {self.kind}")
        if self.index < 0:
            raise ValueError("MediaItem.index nao pode ser negativo")

    @property
    def is_remote(self) -> bool:
        return urlparse(self.source).scheme.lower() in {"http", "https"}

    @property
    def path(self) -> Path | None:
        return None if self.is_remote else Path(self.source)

    def with_source(self, source: str, **changes: Any) -> "MediaItem":
        return replace(self, source=source, **changes)


@dataclass(frozen=True)
class MediaBundle:
    platform: str
    items: tuple[MediaItem, ...]
    title: str = ""
    author: str = ""
    text: str = ""
    source_url: str = ""
    source_id: str = ""
    expected_items: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.items, key=lambda item: item.index))
        if ordered != self.items:
            object.__setattr__(self, "items", ordered)
        if self.expected_items is not None and self.expected_items < 0:
            raise ValueError("expected_items nao pode ser negativo")

    @property
    def is_partial(self) -> bool:
        return self.expected_items is not None and len(self.items) < self.expected_items

    def require_media(self) -> "MediaBundle":
        if not self.items:
            raise ValueError("O pacote nao contem midia")
        return self

    @classmethod
    def from_legacy_result(
        cls,
        result: dict[str, Any],
        *,
        platform: str,
        source_url: str = "",
    ) -> "MediaBundle":
        sources = list(result.get("files") or result.get("urls") or [])
        explicit_type = str(result.get("type") or "").lower()
        items: list[MediaItem] = []
        for index, source in enumerate(sources):
            suffix = Path(urlparse(str(source)).path).suffix.lower()
            kind: MediaKind
            if suffix in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
                kind = "video"
            elif explicit_type == "video" and len(sources) == 1:
                kind = "video"
            else:
                kind = "photo"
            items.append(MediaItem(str(source), kind, index=index))
        expected = result.get("expected_items")
        return cls(
            platform=platform,
            items=tuple(items),
            title=str(result.get("title") or ""),
            author=str(result.get("uploader") or result.get("author") or ""),
            source_url=source_url,
            source_id=str(result.get("id") or ""),
            expected_items=int(expected) if isinstance(expected, int) else None,
            metadata={
                key: value
                for key, value in result.items()
                if key not in {"files", "urls", "title", "uploader", "author"}
            },
        )
