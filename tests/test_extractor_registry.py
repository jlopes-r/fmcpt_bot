import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.errors import UnsupportedUrl
from apps.telegram_bot.extractors.facebook import FacebookExtractor
from apps.telegram_bot.extractors.generic import GenericYtDlpExtractor, is_generic_content_url
from apps.telegram_bot.extractors.instagram import InstagramExtractor
from apps.telegram_bot.extractors.registry import ExtractorRegistry, build_default_registry
from apps.telegram_bot.extractors.twitter import TwitterExtractor
from apps.telegram_bot.models.media import MediaBundle, MediaItem
from apps.telegram_bot.services.download_manager import (
    PLATFORM_DOWNLOAD_POLICIES,
    DownloadManager,
    build_download_options,
)


class ExtractorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.manager = DownloadManager(Path(tempfile.gettempdir()) / "extractor-tests")
        self.registry = build_default_registry(SimpleNamespace(), self.manager)

    def test_specific_extractors_are_ordered_before_generic(self):
        self.assertEqual(
            [type(item) for item in self.registry.extractors],
            [InstagramExtractor, FacebookExtractor, TwitterExtractor, GenericYtDlpExtractor],
        )

    def test_resolves_each_supported_platform(self):
        cases = {
            "https://instagram.com/p/Abc123/": InstagramExtractor,
            "https://facebook.com/reel/123": FacebookExtractor,
            "https://x.com/example/status/123": TwitterExtractor,
            "https://youtube.com/watch?v=abc123": GenericYtDlpExtractor,
            "https://tiktok.com/@example/video/123": GenericYtDlpExtractor,
            "https://threads.net/@example/post/AbC": GenericYtDlpExtractor,
            "https://pinterest.com/pin/123/": GenericYtDlpExtractor,
        }
        for url, expected_type in cases.items():
            with self.subTest(url=url):
                self.assertIsInstance(self.registry.resolve(url), expected_type)

    def test_rejects_profiles_and_unknown_domains(self):
        for url in (
            "https://instagram.com/example/",
            "https://facebook.com/example/",
            "https://youtube.com/@example",
            "https://pinterest.com/example/",
            "https://example.com/video/123",
        ):
            with self.subTest(url=url), self.assertRaises(UnsupportedUrl):
                self.registry.resolve(url)

    def test_register_prepend_controls_priority(self):
        first = SimpleNamespace(supports=lambda url: True)
        second = SimpleNamespace(supports=lambda url: True)
        registry = ExtractorRegistry((first,))
        registry.register(second, prepend=True)
        self.assertIs(registry.resolve("https://example.com"), second)


class GenericExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_threads_enables_bounded_multi_media_download(self):
        bundle = MediaBundle("threads", (MediaItem("one.jpg", "photo"),))
        manager = SimpleNamespace(download=AsyncMock(return_value=bundle))
        extractor = GenericYtDlpExtractor(manager)

        result = await extractor.extract("https://threads.net/@example/post/ABC")

        self.assertIs(result, bundle)
        manager.download.assert_awaited_once_with(
            "https://threads.net/@example/post/ABC",
            platform="threads",
            allow_playlist=True,
            playlist_limit=20,
        )

    def test_short_urls_are_supported_but_profiles_are_not(self):
        self.assertTrue(is_generic_content_url("https://vm.tiktok.com/ZMabc/"))
        self.assertTrue(is_generic_content_url("https://pin.it/abc123"))
        self.assertFalse(is_generic_content_url("https://pinterest.com/example/"))


class InstagramAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_pool_result_is_normalized(self):
        legacy = AsyncMock(return_value={
            "urls": ["https://cdn.example/one.jpg", "https://cdn.example/two.mp4"],
            "title": "Caption",
            "uploader": "Ada",
            "_cookie_source": "secondary",
        })
        manager = SimpleNamespace(
            download_root=Path(tempfile.gettempdir()),
            download=AsyncMock(),
        )
        extractor = InstagramExtractor(
            manager,
            cookie_path="primary.txt",
            secondary_cookie_path="secondary.txt",
            legacy_download=legacy,
        )

        result = await extractor.extract("https://instagram.com/p/ABC123/")

        self.assertEqual([item.kind for item in result.items], ["photo", "video"])
        self.assertEqual(result.source_id, "ABC123")
        self.assertEqual(result.metadata["extraction_strategy"], "instagram-account-pool")
        manager.download.assert_not_awaited()

    async def test_failed_account_pool_falls_back_to_download_manager(self):
        fallback = MediaBundle("instagram", (MediaItem("one.mp4", "video"),))
        manager = SimpleNamespace(
            download_root=Path(tempfile.gettempdir()),
            download=AsyncMock(return_value=fallback),
        )
        extractor = InstagramExtractor(manager, legacy_download=AsyncMock(return_value=None))

        result = await extractor.extract("https://instagram.com/reel/ABC123/")

        self.assertEqual(result.metadata["extraction_strategy"], "yt-dlp-fallback")
        manager.download.assert_awaited_once_with(
            "https://instagram.com/reel/ABC123/",
            platform="instagram",
            allow_playlist=False,
            playlist_limit=20,
        )


class DownloadPolicyTests(unittest.TestCase):
    def test_each_web_platform_declares_cookie_and_fallback_policy(self):
        for platform in ("youtube", "tiktok", "threads", "pinterest", "facebook", "instagram"):
            with self.subTest(platform=platform):
                policy = PLATFORM_DOWNLOAD_POLICIES[platform]
                self.assertTrue(policy.cookie_env)
                self.assertTrue(policy.fallback)

    def test_threads_options_apply_impersonation_referer_and_cookie(self):
        with tempfile.TemporaryDirectory() as folder:
            cookie = Path(folder) / "cookies.txt"
            cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"THREADS_COOKIE_PATH": str(cookie), "THREADS_IMPERSONATE": "chrome"},
            ):
                options = build_download_options(
                    "threads",
                    Path(folder),
                    max_filesize=123,
                    allow_playlist=True,
                )

        self.assertEqual(options["impersonate"], "chrome")
        self.assertEqual(options["http_headers"]["Referer"], "https://www.threads.net/")
        self.assertEqual(options["_source_cookiefile"], str(cookie))
        self.assertFalse(options["noplaylist"])


if __name__ == "__main__":
    unittest.main()
