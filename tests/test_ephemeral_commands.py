import unittest

from apps.ephemeral_commands.service import BotRuntime, EphemeralCommandService, parse_command
from packages.command_catalog import CommandSpec


class EphemeralCommandServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_command_strips_bot_username_and_args(self):
        self.assertEqual(parse_command("/menu@fmcpt_bot agora"), ("menu", ["agora"]))
        self.assertEqual(parse_command("menu"), ("", []))

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
