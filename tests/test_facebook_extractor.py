import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.errors import AuthenticationRequired, ContentUnavailable
from apps.telegram_bot.extractors.facebook import FacebookExtractor
from apps.telegram_bot.facebook import (
    extract_canonical_facebook_url,
    parse_facebook_target,
    parse_public_post,
)
from apps.telegram_bot.models.media import MediaBundle, MediaItem


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "facebook"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FacebookTargetTests(unittest.TestCase):
    def test_derives_expected_id_for_all_supported_content_kinds(self):
        cases = {
            "https://www.facebook.com/example/posts/111": ("post", "111"),
            "https://www.facebook.com/permalink.php?story_fbid=222&id=9": ("post", "222"),
            "https://www.facebook.com/reel/333/": ("reel", "333"),
            "https://www.facebook.com/stories/example/444/": ("story", "444"),
            "https://www.facebook.com/watch/?v=555": ("video", "555"),
            "https://www.facebook.com/video.php?v=666": ("video", "666"),
            "https://www.facebook.com/example/videos/777/": ("video", "777"),
            "https://www.facebook.com/share/r/opaque/": ("reel", ""),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                target = parse_facebook_target(url)
                self.assertIsNotNone(target)
                self.assertEqual((target.kind, target.content_id), expected)

    def test_non_facebook_url_has_no_target(self):
        self.assertIsNone(parse_facebook_target("https://example.com/posts/1"))


class FacebookHtmlFixtureTests(unittest.TestCase):
    def test_selects_requested_story_instead_of_recommendation(self):
        html = fixture("post_with_recommendation.html")
        result = parse_public_post(html, "222", "post")

        self.assertEqual(result["text"], "Requested post")
        self.assertEqual(result["author"], "Example Author")
        self.assertEqual(
            [item["type"] for item in result["media"]],
            ["photo", "video", "photo"],
        )
        self.assertNotIn("recommended.jpg", " ".join(result["photos"]))
        self.assertNotIn("thumbnail.jpg", " ".join(result["photos"]))
        self.assertEqual(result["expected_items"], 3)
        self.assertEqual(
            extract_canonical_facebook_url(html),
            "https://www.facebook.com/example/posts/222",
        )

    def test_mismatched_expected_id_is_rejected(self):
        self.assertIsNone(
            parse_public_post(fixture("post_with_recommendation.html"), "999", "post")
        )

    def test_reel_story_and_video_fixtures(self):
        cases = (
            ("reel.html", "333", "reel", "video"),
            ("story.html", "444", "story", "photo"),
            ("video.html", "555", "video", "video"),
        )
        for name, source_id, content_type, media_type in cases:
            with self.subTest(name=name):
                result = parse_public_post(fixture(name), source_id, content_type)
                self.assertEqual(result["source_id"], source_id)
                self.assertEqual(result["content_type"], content_type)
                self.assertEqual(result["media"][0]["type"], media_type)


class FacebookExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_post_uses_public_html_without_ytdlp(self):
        manager = SimpleNamespace(download=AsyncMock())
        extractor = FacebookExtractor(SimpleNamespace(), manager)
        post = parse_public_post(fixture("story.html"), "444", "post")

        with patch(
            "apps.telegram_bot.extractors.facebook.fetch_public_post",
            new=AsyncMock(return_value=post),
        ):
            result = await extractor.extract("https://facebook.com/example/posts/444")

        manager.download.assert_not_awaited()
        self.assertEqual(result.metadata["extraction_strategy"], "public-html")
        self.assertEqual(result.items[0].kind, "photo")

    async def test_video_uses_ytdlp_first(self):
        downloaded = MediaBundle(
            "facebook",
            (MediaItem("local.mp4", "video"),),
            source_id="555",
        )
        manager = SimpleNamespace(download=AsyncMock(return_value=downloaded))
        extractor = FacebookExtractor(SimpleNamespace(), manager)

        with patch(
            "apps.telegram_bot.extractors.facebook.fetch_public_post",
            new=AsyncMock(),
        ) as fetch:
            result = await extractor.extract("https://facebook.com/watch/?v=555")

        fetch.assert_not_awaited()
        manager.download.assert_awaited_once_with(
            "https://facebook.com/watch/?v=555",
            platform="facebook",
            allow_playlist=False,
            playlist_limit=20,
        )
        self.assertEqual(result.metadata["extraction_strategy"], "yt-dlp")

    async def test_reel_falls_back_from_ytdlp_to_public_html(self):
        manager = SimpleNamespace(
            download=AsyncMock(side_effect=ContentUnavailable("failed", platform="facebook"))
        )
        extractor = FacebookExtractor(SimpleNamespace(), manager)
        post = parse_public_post(fixture("reel.html"), "333", "reel")

        with patch(
            "apps.telegram_bot.extractors.facebook.fetch_public_post",
            new=AsyncMock(return_value=post),
        ):
            result = await extractor.extract("https://facebook.com/reel/333")

        self.assertEqual(result.items[0].source, "https://video.xx.fbcdn.net/reel-333.mp4")
        self.assertEqual(result.metadata["extraction_strategy"], "public-html-fallback")

    async def test_mixed_post_merges_downloaded_video_in_html_order(self):
        post = parse_public_post(fixture("post_with_recommendation.html"), "222", "post")
        downloaded = MediaBundle(
            "facebook",
            (MediaItem("local-video.mp4", "video"),),
            source_id="222",
            expected_items=1,
        )
        manager = SimpleNamespace(download=AsyncMock(return_value=downloaded))
        extractor = FacebookExtractor(SimpleNamespace(), manager)

        with patch(
            "apps.telegram_bot.extractors.facebook.fetch_public_post",
            new=AsyncMock(return_value=post),
        ):
            result = await extractor.extract("https://facebook.com/example/posts/222")

        self.assertEqual([item.kind for item in result.items], ["photo", "video", "photo"])
        self.assertEqual(result.items[1].source, "local-video.mp4")
        self.assertEqual(result.metadata["extraction_strategy"], "html+yt-dlp")

    async def test_authentication_error_wins_when_both_fallbacks_fail(self):
        manager = SimpleNamespace(
            download=AsyncMock(side_effect=ContentUnavailable("failed", platform="facebook"))
        )
        extractor = FacebookExtractor(SimpleNamespace(), manager)

        with patch(
            "apps.telegram_bot.extractors.facebook.fetch_public_post",
            new=AsyncMock(side_effect=__import__(
                "apps.telegram_bot.facebook", fromlist=["FacebookAccessRestricted"]
            ).FacebookAccessRestricted()),
        ):
            with self.assertRaises(AuthenticationRequired):
                await extractor.extract("https://facebook.com/watch/?v=555")


if __name__ == "__main__":
    unittest.main()
