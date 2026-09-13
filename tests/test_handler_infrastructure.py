import unittest
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.handlers.callbacks import ExpiringRegistry
from apps.telegram_bot.handlers.commands import build_admin_only
from apps.telegram_bot.handlers.links import InFlightLinks, extract_supported_url
from apps.telegram_bot.handlers.moderation import SlidingWindowLimiter


class LinkRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_only_allowlisted_hosts(self):
        domains = ("instagram.com", "x.com")
        self.assertEqual(
            extract_supported_url("olha x.com/a/status/1", domains),
            "https://x.com/a/status/1",
        )
        self.assertIsNone(extract_supported_url("https://x.com.evil.test/a", domains))

    async def test_inflight_claim_is_atomic(self):
        registry = InFlightLinks(ttl=60)
        first, second = await __import__("asyncio").gather(
            registry.claim("same"), registry.claim("same")
        )
        self.assertEqual(sorted((first, second)), [False, True])
        await registry.release("same")
        self.assertTrue(await registry.claim("same"))


class HandlerPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_decorator_stops_unauthorized_call(self):
        called = AsyncMock()
        decorated = build_admin_only(lambda: 10)(called)
        message = type(
            "Message",
            (),
            {"from_user": type("User", (), {"id": 11})(), "reply_text": AsyncMock()},
        )()
        await decorated(None, message)
        called.assert_not_awaited()
        message.reply_text.assert_awaited_once()

    def test_limiter_uses_sliding_window(self):
        limiter = SlidingWindowLimiter(limit=2, window_seconds=10)
        self.assertTrue(limiter.allow(1, now=0))
        self.assertTrue(limiter.allow(1, now=1))
        self.assertFalse(limiter.allow(1, now=2))
        self.assertTrue(limiter.allow(1, now=11))

    def test_callback_registry_is_bounded_and_expires(self):
        registry = ExpiringRegistry(ttl=10, max_entries=2)
        with patch("apps.telegram_bot.handlers.callbacks.time.monotonic", return_value=0):
            registry[1] = "one"
            registry[2] = "two"
        with patch("apps.telegram_bot.handlers.callbacks.time.monotonic", return_value=1):
            registry[3] = "three"
        self.assertNotIn(1, registry)
        with patch("apps.telegram_bot.handlers.callbacks.time.monotonic", return_value=20):
            self.assertEqual(len(registry), 0)


if __name__ == "__main__":
    unittest.main()

