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
            async def send_bundle(*_args, **kwargs):
                await kwargs["upload_started"]()
                return bundle

            pipeline.sender.send = AsyncMock(side_effect=send_bundle)
            message = SimpleNamespace(
                chat=SimpleNamespace(id=1),
                id=2,
                reply_text=AsyncMock(),
            )
            status = SimpleNamespace(delete=AsyncMock())
            upload_started = AsyncMock()

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
                    upload_started=upload_started,
                )

            self.assertEqual(result.item_count, 1)
            self.assertFalse(media_path.exists())
            extractor.extract.assert_awaited_once()
            upload_started.assert_awaited_once()

    async def test_long_media_does_not_enter_uploading_state(self):
        bundle = MediaBundle(
            "instagram",
            (MediaItem("long.mp4", "video", duration=601),),
        )
        extractor = SimpleNamespace(extract=AsyncMock(return_value=bundle))
        pipeline = SocialMediaPipeline(
            client=SimpleNamespace(),
            session=SimpleNamespace(),
            config=SocialPipelineConfig(
                download_root=Path(tempfile.gettempdir()),
                max_media_bytes=10_000,
                download_timeout=30,
                duration_limit=600,
            ),
            registry=SimpleNamespace(resolve=lambda _url: extractor),
        )
        upload_started = AsyncMock()

        result = await pipeline.deliver(
            message=SimpleNamespace(),
            url="https://instagram.com/p/long/",
            requested_by="Juan",
            status=SimpleNamespace(),
            long_video_callback=AsyncMock(),
            upload_started=upload_started,
        )

        self.assertTrue(result.skipped)
        upload_started.assert_not_awaited()


class EntrypointArchitectureTests(unittest.TestCase):
    def test_super_bot_has_no_legacy_media_handlers(self):
        source = Path("apps/telegram_bot/super_bot.py").read_text(encoding="utf-8")
        self.assertNotIn("async def extrair_e_enviar_midia", source)
        self.assertNotIn("async def processar_instagram(", source)
        self.assertNotIn("async def processar_facebook_pipeline", source)
        self.assertIn("SocialMediaPipeline(", source)
        self.assertIn("pipeline.deliver(", source)
        self.assertIn("_job_runtime.submit(", source)
        self.assertIn("_job_runtime.recover_startup(", source)
        self.assertIn("await verificar_rate_limit(user_id)", source)


if __name__ == "__main__":
    unittest.main()
