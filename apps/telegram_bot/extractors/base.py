"""Contrato comum para extratores sociais."""

from __future__ import annotations

from abc import ABC, abstractmethod

from apps.telegram_bot.models.media import MediaBundle


class SocialExtractor(ABC):
    platform: str = ""

    @abstractmethod
    def supports(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def extract(self, url: str) -> MediaBundle:
        raise NotImplementedError
