"""Primitivas de moderacao independentes do framework Telegram."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1.0, window_seconds)
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        events = self._events[user_id]
        cutoff = current - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(current)
        return True

    def prune(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        removed = 0
        for user_id in tuple(self._events):
            events = self._events[user_id]
            cutoff = current - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._events[user_id]
                removed += 1
        return removed

