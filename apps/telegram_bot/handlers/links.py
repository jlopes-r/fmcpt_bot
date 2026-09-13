"""Roteamento seguro e coordenacao local dos links recebidos."""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse


URL_RE = re.compile(
    r"((?:https?://|www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)"
)


def is_supported_url(url: str, domains: tuple[str, ...] | list[str]) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return False
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            return False
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)
    except (TypeError, ValueError):
        return False


def extract_supported_url(
    text: str,
    domains: tuple[str, ...] | list[str],
) -> str | None:
    match = URL_RE.search(text or "")
    if not match:
        return None
    url = match.group(1)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url if is_supported_url(url, domains) else None


class InFlightLinks:
    """Evita duas extracoes locais simultaneas da mesma URL normalizada."""

    def __init__(self, *, ttl: float = 7200) -> None:
        self.ttl = max(1.0, ttl)
        self.entries: dict[str, float] = {}
        self.lock = asyncio.Lock()

    async def claim(self, url: str) -> bool:
        async with self.lock:
            self._prune_unlocked()
            if url in self.entries:
                return False
            self.entries[url] = time.monotonic()
            return True

    async def release(self, url: str) -> None:
        async with self.lock:
            self.entries.pop(url, None)

    async def prune(self) -> int:
        async with self.lock:
            return self._prune_unlocked()

    def _prune_unlocked(self) -> int:
        now = time.monotonic()
        expired = [url for url, started in self.entries.items() if now - started > self.ttl]
        for url in expired:
            self.entries.pop(url, None)
        return len(expired)

