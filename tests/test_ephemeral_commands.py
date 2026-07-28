import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.ephemeral_commands import service as eph
from apps.ephemeral_commands.service import BotRuntime, EphemeralCommandService, parse_command
from packages.command_catalog import CommandSpec


class EphemeralCommandServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_command_strips_bot_username_and_args(self):
        self.assertEqual(parse_command("/menu@fmcpt_bot agora"), ("menu", ["agora"]))
        self.assertEqual(parse_command("menu"), ("", []))

    def test_custom_command_list_keeps_commands_clickable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "comandos_personalizados.json").write_text(
                '{"sorry": {"tipo": "texto"}, "teste": {"tipo": "gif"}}',
                encoding="utf-8",
            )
            with patch.object(eph, "DATA_DIR", data_dir):
                text = eph.build_custom_command_list()

        self.assertIn("<b>📋 Comandos Personalizados:</b>", text)
        self.assertIn("/sorry", text)
        self.assertIn("/teste", text)
        self.assertNotIn("<code>/sorry</code>", text)

    async def test_handle_menu_sends_ephemeral_message_to_sender(self):
        service = EphemeralCommandService()
        service.allowed_chats = {-100123}
        seen = []

        async def fake_send(session, api, chat_id, user_id, text, parse_mode=None):
            seen.append((chat_id, user_id, text, parse_mode))

        service.send_ephemeral = fake_send
        runtime = BotRuntime(
            "super",
            "123:abc",
            (CommandSpec("menu", "Mostra o menu", "Interface", ephemeral=True),),
            "Menu",
        )
        message = {
            "chat": {"id": -100123, "type": "supergroup"},
            "from": {"id": 456},
            "text": "/menu",
        }

        await service.handle_command(None, runtime, None, message)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], -100123)
        self.assertEqual(seen[0][1], 456)
        self.assertIn("<b>Menu</b>", seen[0][2])
        self.assertEqual(seen[0][3], "HTML")


if __name__ == "__main__":
    unittest.main()
