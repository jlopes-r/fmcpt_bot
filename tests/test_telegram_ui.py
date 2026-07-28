import unittest

from packages.command_catalog import CommandSpec
from packages.telegram_ui import build_bot_commands_payload, build_mini_app_markup_payload, reply_command_menu


class ButtonTypeInvalid(Exception):
    pass


class FakeMessage:
    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.calls = []

    async def reply_text(self, text, reply_markup=None):
        self.calls.append({"text": text, "reply_markup": reply_markup})
        if self.fail_first and len(self.calls) == 1:
            raise ButtonTypeInvalid("Telegram says BUTTON_TYPE_INVALID")
        return "sent"


class TelegramUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_command_menu_falls_back_when_web_app_button_is_rejected(self):
        message = FakeMessage(fail_first=True)
        commands = (
            CommandSpec("menu", "Mostra o menu", "Sistema"),
        )

        result = await reply_command_menu(message, "Menu", commands, "https://example.com")

        self.assertEqual(result, "sent")
        self.assertEqual(len(message.calls), 2)
        self.assertIsNotNone(message.calls[0]["reply_markup"])
        self.assertIsNone(message.calls[1]["reply_markup"])
        self.assertIn("Abra o bot no privado", message.calls[1]["text"])

    async def test_reply_command_menu_without_url_sends_plain_text(self):
        message = FakeMessage()
        commands = (
            CommandSpec("menu", "Mostra o menu", "Sistema"),
        )

        await reply_command_menu(message, "Menu", commands, "")

        self.assertEqual(len(message.calls), 1)
        self.assertIsNone(message.calls[0]["reply_markup"])

    async def test_build_bot_commands_payload_marks_ephemeral_commands(self):
        commands = (
            CommandSpec("menu", "Mostra o menu", "Sistema", ephemeral=True),
            CommandSpec("comi", "Escolhe alguem", "Diversao"),
        )

        payload = build_bot_commands_payload(commands)

        self.assertEqual(payload[0]["command"], "menu")
        self.assertTrue(payload[0]["is_ephemeral"])
        self.assertEqual(payload[1]["command"], "comi")
        self.assertNotIn("is_ephemeral", payload[1])

    async def test_build_mini_app_markup_payload_uses_web_app_button(self):
        payload = build_mini_app_markup_payload("https://example.com")

        self.assertEqual(
            payload,
            {
                "inline_keyboard": [
                    [{"text": "Abrir painel", "web_app": {"url": "https://example.com"}}],
                ]
            },
        )


if __name__ == "__main__":
    unittest.main()
