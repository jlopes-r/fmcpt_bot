import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.errors import ContentUnavailable
from apps.telegram_bot.extractors.twitter import TwitterExtractor, normalize_tweet_payload
from apps.telegram_bot.models.media import MediaBundle, MediaItem


class TwitterNormalizerTests(unittest.TestCase):
    def test_vx_mixed_media_order_and_identity(self):
        payload = {
            "tweetID": "123",
            "tweetURL": "https://x.com/ada/status/123",
            "text": "hello",
            "user_name": "Ada",
            "media_extended": [
                {"type": "image", "url": "https://pbs.twimg.com/one.jpg"},
                {"type": "video", "url": "https://video.twimg.com/two.mp4", "duration_millis": 2500},
                {"type": "image", "url": "https://pbs.twimg.com/three.jpg"},
            ],
        }
        bundle = normalize_tweet_payload(payload, expected_id="123")
        self.assertEqual([item.kind for item in bundle.items], ["photo", "video", "photo"])
        self.assertEqual(bundle.items[1].duration, 2.5)
        self.assertFalse(bundle.is_partial)

    def test_fx_uses_media_all_and_best_h264_mp4(self):
        payload = {"tweet": {
            "id": "123",
            "url": "https://x.com/ada/status/123",
            "author": {"name": "Ada", "screen_name": "ada"},
            "media": {"all": [{
                "id": "v1", "type": "video", "url": "https://video.twimg.com/base.m3u8",
                "width": 1280, "height": 720, "duration": 3,
                "formats": [
                    {"container": "mp4", "codec": "h264", "height": 480, "bitrate": 1, "url": "https://video.twimg.com/480.mp4"},
                    {"container": "mp4", "codec": "h264", "height": 720, "bitrate": 2, "url": "https://video.twimg.com/720.mp4"},
                ],
            }]},
        }}
        bundle = normalize_tweet_payload(payload, expected_id="123")
        self.assertEqual(bundle.items[0].source, "https://video.twimg.com/720.mp4")
        self.assertEqual(bundle.metadata["screen_name"], "ada")

    def test_rejects_response_for_another_tweet(self):
        with self.assertRaises(ContentUnavailable):
            normalize_tweet_payload({"tweetID": "999"}, expected_id="123")


class TwitterExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_failure_downloads_whole_tweet_once(self):
        manager = SimpleNamespace(
            download=AsyncMock(return_value=MediaBundle(
                "twitter", (MediaItem("one.mp4", "video"),), source_id="123"
            ))
        )
        extractor = TwitterExtractor(SimpleNamespace(), manager)
        with patch.object(
            extractor,
            "_fetch",
            new=AsyncMock(side_effect=ContentUnavailable("failed")),
        ):
            result = await extractor.extract("https://x.com/ada/status/123")
        self.assertEqual(result.source_id, "123")
        manager.download.assert_awaited_once_with(
            "https://x.com/ada/status/123",
            platform="twitter",
            allow_playlist=True,
            playlist_limit=20,
        )
