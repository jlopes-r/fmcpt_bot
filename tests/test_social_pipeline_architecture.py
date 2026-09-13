import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.extractors.base import ExtractionContext
from apps.telegram_bot.extractors.generic import GenericYtDlpExtractor
from apps.telegram_bot.handlers.social import SocialMediaPipeline, SocialPipelineConfig
from apps.telegram_bot.models.media import MediaBundle, MediaItem


class ExtractionContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_extractor_forwards_request_context(self):
        bundle = MediaBundle("threads", (MediaItem("post.mp4", "video"),))
        manager = SimpleNamespace(download=AsyncMock(return_value=bundle))
        extractor = GenericYtDlpExtractor(manager)
        status = object()
        cancel_event = threading.Event()
        markup = object()
        context = ExtractionContext(
            duration_limit=600,
            status=status,
            cancel_event=cancel_event,
            reply_markup=markup,
            playlist_limit=12,
        )

        result = await extractor.extract(
            "https://threads.net/@example/post/ABC",
            context=context,
        )

        self.assertIs(result, bundle)
        manager.download.assert_awaited_once_with(
            "https://threads.net/@example/post/ABC",
            platform="threads",
            allow_playlist=True,
            playlist_limit=12,
            duration_limit=600,
            status=status,
            cancel_event=cancel_event,
            reply_markup=markup,
        )


class SocialPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_uses_registry_and_removes_owned_local_media(self):
        with tempfile.TemporaryDirectory() as folder:
            media_path = Path(folder) / "owned.jpg"
            media_path.write_bytes(b"not-read-because-sender-is-mocked")
            bundle = MediaBundle(
                "instagram",
                (MediaItem(str(media_path), "photo"),),
                text="Post",
            )
            extractor = SimpleNamespace(extract=AsyncMock(return_value=bundle))
            registry = SimpleNamespace(resolve=lambda _url: extractor)
            pipeline = SocialMediaPipeline(
                client=SimpleNamespace(),
                session=SimpleNamespace(),
                config=SocialPipelineConfig(
                    download_root=Path(folder),
                    max_media_bytes=10_000,
                    download_timeout=30,
                    duration_limit=600,
                ),
                registry=registry,
            )
            pipeline.sender.send = AsyncMock(return_value=bundle)
            message = SimpleNamespace(
                chat=SimpleNamespace(id=1),
                id=2,
                reply_text=AsyncMock(),
            )
            status = SimpleNamespace(delete=AsyncMock())

            with patch(
                "apps.telegram_bot.handlers.social.traduzir_se_necessario",
                side_effect=lambda text: text,
            ):
                result = await pipeline.deliver(
                    message=message,
                    url="https://instagram.com/p/ABC/",
                    requested_by="Juan",
                    status=status,
                    long_video_callback=AsyncMock(),
                )

            self.assertEqual(result.item_count, 1)
            self.assertFalse(media_path.exists())
            extractor.extract.assert_awaited_once()


class EntrypointArchitectureTests(unittest.TestCase):
    def test_super_bot_has_no_legacy_media_handlers(self):
        source = Path("apps/telegram_bot/super_bot.py").read_text(encoding="utf-8")
        self.assertNotIn("async def extrair_e_enviar_midia", source)
        self.assertNotIn("async def processar_instagram(", source)
        self.assertNotIn("async def processar_facebook_pipeline", source)
        self.assertIn("SocialMediaPipeline(", source)
        self.assertIn("pipeline.deliver(", source)


if __name__ == "__main__":
    unittest.main()
