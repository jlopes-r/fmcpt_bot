"""Armazenamento limitado para estados efemeros de callbacks."""

from __future__ import annotations

import time
from collections.abc import Iterator, MutableMapping
from typing import Generic, TypeVar


T = TypeVar("T")


class ExpiringRegistry(MutableMapping[str | int, T], Generic[T]):
    def __init__(self, *, ttl: float = 3600, max_entries: int = 500) -> None:
        self.ttl = max(1.0, ttl)
        self.max_entries = max(1, max_entries)
        self._values: dict[str | int, tuple[float, T]] = {}

    def __getitem__(self, key: str | int) -> T:
        self.prune()
        return self._values[key][1]

    def __setitem__(self, key: str | int, value: T) -> None:
        self.prune()
        if key not in self._values and len(self._values) >= self.max_entries:
            oldest = min(self._values, key=lambda item: self._values[item][0])
            self._values.pop(oldest, None)
        self._values[key] = (time.monotonic(), value)

    def __delitem__(self, key: str | int) -> None:
        del self._values[key]

    def __iter__(self) -> Iterator[str | int]:
        self.prune()
        return iter(tuple(self._values))

    def __len__(self) -> int:
        self.prune()
        return len(self._values)

    def clear(self) -> None:
        self._values.clear()

    def prune(self) -> int:
        cutoff = time.monotonic() - self.ttl
        expired = [key for key, (created, _) in self._values.items() if created < cutoff]
        for key in expired:
            self._values.pop(key, None)
        return len(expired)

