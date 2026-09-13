import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apps.telegram_bot.handlers.twitter import deliver_twitter_post
from apps.telegram_bot.models.media import MediaBundle, MediaItem


class TwitterHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_main_and_quote_each_use_unified_sender(self):
        quote = MediaBundle(
            "twitter",
            (
                MediaItem("quote.jpg", "photo", index=0),
                MediaItem("quote.mp4", "video", index=1),
            ),
            text="quoted",
            author="Quote",
        )
        main = MediaBundle(
            "twitter",
            (
                MediaItem("main.jpg", "photo", index=0),
                MediaItem("main.mp4", "video", index=1),
            ),
            text="main",
            author="Main",
            metadata={"raw": {"text": "main", "lang": "pt"}, "quote": quote},
        )
        extractor = SimpleNamespace(extract=AsyncMock(return_value=main))
        sender = SimpleNamespace(send=AsyncMock(side_effect=[main, quote]))
        status = SimpleNamespace(delete=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock())
        outcome = await deliver_twitter_post(
            client=object(),
            message=message,
            url="https://x.com/main/status/1",
            requested_by="User",
            status=status,
            extractor=extractor,
            sender=sender,
            duration_limit=600,
            long_video_callback=AsyncMock(),
        )
        self.assertEqual(outcome.main_items, 2)
        self.assertEqual(outcome.quote_items, 2)
        self.assertEqual(sender.send.await_count, 2)
        self.assertEqual(
            [item.kind for item in sender.send.await_args_list[1].args[1].items],
            ["photo", "video"],
        )

    async def test_single_photo_still_uses_sender(self):
        bundle = MediaBundle(
            "twitter",
            (MediaItem("one.jpg", "photo"),),
            text="main",
            author="Main",
            metadata={"raw": {"text": "main", "lang": "pt"}},
        )
        extractor = SimpleNamespace(extract=AsyncMock(return_value=bundle))
        sender = SimpleNamespace(send=AsyncMock(return_value=bundle))
        await deliver_twitter_post(
            client=object(),
            message=SimpleNamespace(reply_text=AsyncMock()),
            url="https://x.com/main/status/1",
            requested_by="User",
            status=SimpleNamespace(delete=AsyncMock()),
            extractor=extractor,
            sender=sender,
            duration_limit=600,
            long_video_callback=AsyncMock(),
        )
        sender.send.assert_awaited_once()

    async def test_long_video_is_marked_as_skipped(self):
        bundle = MediaBundle(
            "twitter",
            (MediaItem("long.mp4", "video", duration=601),),
        )
        callback = AsyncMock()
        outcome = await deliver_twitter_post(
            client=object(),
            message=SimpleNamespace(reply_text=AsyncMock()),
            url="https://x.com/main/status/1",
            requested_by="User",
            status=SimpleNamespace(delete=AsyncMock()),
            extractor=SimpleNamespace(extract=AsyncMock(return_value=bundle)),
            sender=SimpleNamespace(send=AsyncMock()),
            duration_limit=600,
            long_video_callback=callback,
        )

        self.assertTrue(outcome.skipped)
        callback.assert_awaited_once()
