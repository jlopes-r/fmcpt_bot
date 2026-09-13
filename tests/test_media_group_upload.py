import unittest
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyrogram.types import InputMediaPhoto, InputMediaVideo


class MediaGroupUploadTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.super_bot = importlib.import_module('apps.telegram_bot.super_bot')

    def _load_super_bot(self):
        return self.super_bot

    async def test_mixed_album_is_sent_once_in_original_order(self):
        super_bot = self._load_super_bot()
        client = SimpleNamespace(send_media_group=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=123), id=456)
        status = SimpleNamespace(edit_text=AsyncMock())
        album = [
            InputMediaPhoto('first.jpg', caption='caption'),
            InputMediaVideo('middle.mp4', supports_streaming=True),
            InputMediaPhoto('last.jpg'),
        ]

        with patch.object(super_bot, '_probe_video_attrs', return_value=(1080, 1920, 12)):
            await super_bot._enviar_album_com_progresso(client, message, album, status)

        client.send_media_group.assert_awaited_once()
        sent = client.send_media_group.await_args.args[1]
        self.assertEqual([type(item) for item in sent], [
            InputMediaPhoto, InputMediaVideo, InputMediaPhoto,
        ])
        self.assertEqual([item.media for item in sent], [
            'first.jpg', 'middle.mp4', 'last.jpg',
        ])
        self.assertEqual(sent[0].caption, 'caption')
        self.assertEqual((sent[1].width, sent[1].height, sent[1].duration), (1080, 1920, 12))

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


if __name__ == '__main__':
    unittest.main()
