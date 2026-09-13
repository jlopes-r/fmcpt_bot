"""Contrato comum para extratores sociais."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from apps.telegram_bot.models.media import MediaBundle


@dataclass(frozen=True)
class ExtractionContext:
    """Opcoes efemeras de uma solicitacao, sem estado global no extrator."""

    duration_limit: float | None = None
    status: Any = None
    cancel_event: Any = None
    reply_markup: Any = None
    playlist_limit: int = 20


class SocialExtractor(ABC):
    platform: str = ""

    @abstractmethod
    def supports(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def extract(
        self,
        url: str,
        *,
        context: ExtractionContext | None = None,
    ) -> MediaBundle:
        raise NotImplementedError


__all__ = ["ExtractionContext", "SocialExtractor"]
