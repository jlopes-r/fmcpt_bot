import unittest
import importlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from apps.telegram_bot.handlers.social import SocialMediaPipeline, SocialPipelineConfig
from apps.telegram_bot.models.media import MediaBundle, MediaItem


class MediaGroupUploadTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.super_bot = importlib.import_module('apps.telegram_bot.super_bot')

    def _load_super_bot(self):
        return self.super_bot

    async def test_instagram_status_checks_both_cookie_accounts(self):
        super_bot = self._load_super_bot()
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=456),
            reply_text=AsyncMock(),
        )
        validations = [
            {'valid': False, 'reason': 'sessao invalida'},
            {'valid': True, 'reason': ''},
        ]
        with (
            patch.object(super_bot, 'ADMIN_ID', 456),
            patch.object(super_bot, 'chat_autorizado', return_value=True),
            patch.object(super_bot, 'inspect_cookie_health', side_effect=['principal', 'secundaria']) as inspect,
            patch.object(super_bot, 'validate_cookie_health', new=AsyncMock(side_effect=validations)) as validate,
        ):
            await super_bot.cmd_ig_status(None, message)

        self.assertEqual(
            [call.args[0] for call in inspect.call_args_list],
            [super_bot.COOKIE_PATH, super_bot.SECONDARY_COOKIE_PATH],
        )
        self.assertEqual(
            [call.args[0] for call in validate.await_args_list],
            [super_bot.COOKIE_PATH, super_bot.SECONDARY_COOKIE_PATH],
        )
        report = '\n'.join(call.args[0] for call in message.reply_text.await_args_list)
        self.assertIn('Conta principal', report)
        self.assertIn('Conta secundaria', report)

    async def test_common_social_pipeline_sends_mixed_bundle_once_in_order(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            id=456,
            reply_text=AsyncMock(),
        )
        status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        bundle = MediaBundle(
            "instagram",
            (
                MediaItem("first.jpg", "photo", index=0),
                MediaItem("middle.mp4", "video", index=1),
                MediaItem("last.jpg", "photo", index=2),
            ),
            text="Legenda",
            author="Ada",
        )
        extractor = SimpleNamespace(extract=AsyncMock(return_value=bundle))
        registry = SimpleNamespace(resolve=lambda _url: extractor)
        send = AsyncMock(return_value=bundle)
        with tempfile.TemporaryDirectory() as folder:
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
            pipeline.sender.send = send
            with patch(
                "apps.telegram_bot.handlers.social.traduzir_se_necessario",
                side_effect=lambda text: text,
            ):
                delivered = await pipeline.deliver(
                    message=message,
                    url="https://instagram.com/p/ABC/",
                    requested_by="Juan",
                    status=status,
                    long_video_callback=AsyncMock(),
                )

        self.assertEqual(delivered.item_count, 3)
        sent_bundle = send.await_args.args[1]
        self.assertEqual(
            [item.source for item in sent_bundle.items],
            ["first.jpg", "middle.mp4", "last.jpg"],
        )
        self.assertIn("Legenda", send.await_args.kwargs["caption"])
        message.reply_text.assert_not_awaited()
        extractor.extract.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
