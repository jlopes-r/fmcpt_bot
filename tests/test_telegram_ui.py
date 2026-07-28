import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.command_catalog import CommandSpec
from packages import telegram_ui
from packages.telegram_ui import (
    build_bot_commands_payload,
    build_chat_command_scope_payload,
    build_command_menu_html,
    build_commands_menu_button_payload,
    build_mini_app_markup_payload,
    build_web_app_menu_button_payload,
    clear_bot_commands_for_chat_via_bot_api,
    reply_command_menu,
    set_bot_commands_for_chat_via_bot_api,
    set_bot_commands_menu_button_via_bot_api,
    set_bot_menu_button_via_bot_api,
)


class ButtonTypeInvalid(Exception):
    pass


class FakeMessage:
    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.calls = []
        self.deleted = False

    async def reply_text(self, text, reply_markup=None):
        self.calls.append({"text": text, "reply_markup": reply_markup})
        if self.fail_first and len(self.calls) == 1:
            raise ButtonTypeInvalid("Telegram says BUTTON_TYPE_INVALID")
        return "sent"

    async def delete(self):
        self.deleted = True


class FakeChatType:
    name = "SUPERGROUP"


class FakeGroupChat:
    id = -100123
    type = FakeChatType()


class FakeUser:
    id = 456


class FakeGroupMessage(FakeMessage):
    chat = FakeGroupChat()
    from_user = FakeUser()


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

    async def test_reply_command_menu_does_not_publicly_fallback_for_group_ephemeral(self):
        message = FakeGroupMessage()
        commands = (
            CommandSpec("menu", "Mostra o menu", "Sistema", ephemeral=True),
        )
        original = telegram_ui._bot_api_post

        async def fail_bot_api(*args, **kwargs):
            raise RuntimeError("Bot API sendMessage failed: test")

        telegram_ui._bot_api_post = fail_bot_api
        try:
            result = await reply_command_menu(
                message,
                "Menu",
                commands,
                "",
                bot_token="123:abc",
                ephemeral=True,
                public_fallback=False,
            )
        finally:
            telegram_ui._bot_api_post = original

        self.assertIsNone(result)
        self.assertEqual(message.calls, [])
        self.assertTrue(message.deleted)

    async def test_reply_command_menu_omits_web_app_button_for_group_ephemeral(self):
        message = FakeGroupMessage()
        commands = (
            CommandSpec("menu", "Mostra o menu", "Sistema", ephemeral=True),
        )
        seen_payloads = []
        original = telegram_ui._bot_api_post

        async def capture_bot_api(token, method, payload):
            seen_payloads.append(payload)
            return {"ok": True, "result": {}}

        telegram_ui._bot_api_post = capture_bot_api
        try:
            await reply_command_menu(
                message,
                "Menu",
                commands,
                "https://example.com",
                bot_token="123:abc",
                ephemeral=True,
                public_fallback=False,
            )
        finally:
            telegram_ui._bot_api_post = original

        self.assertEqual(len(seen_payloads), 1)
        self.assertEqual(seen_payloads[0]["receiver_user_id"], 456)
        self.assertNotIn("reply_markup", seen_payloads[0])
        self.assertTrue(message.deleted)

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

    async def test_build_web_app_menu_button_payload(self):
        self.assertEqual(
            build_web_app_menu_button_payload("https://example.com", "Painel"),
            {
                "type": "web_app",
                "text": "Painel",
                "web_app": {"url": "https://example.com"},
            },
        )

    async def test_build_commands_menu_button_payload(self):
        self.assertEqual(build_commands_menu_button_payload(), {"type": "commands"})

    async def test_set_menu_button_can_target_private_chat(self):
        seen = []
        original = telegram_ui._bot_api_post

        async def capture_bot_api(token, method, payload):
            seen.append((token, method, payload))
            return {"ok": True, "result": {}}

        telegram_ui._bot_api_post = capture_bot_api
        try:
            await set_bot_menu_button_via_bot_api("123:abc", "https://example.com", chat_id=456)
            await set_bot_commands_menu_button_via_bot_api("123:abc", chat_id=456)
        finally:
            telegram_ui._bot_api_post = original

        self.assertEqual(seen[0][1], "setChatMenuButton")
        self.assertEqual(seen[0][2]["chat_id"], 456)
        self.assertEqual(seen[0][2]["menu_button"]["type"], "web_app")
        self.assertEqual(seen[1][2], {"menu_button": {"type": "commands"}, "chat_id": 456})

    async def test_set_commands_can_target_private_chat_scope(self):
        seen = []
        original = telegram_ui._bot_api_post

        async def capture_bot_api(token, method, payload):
            seen.append((token, method, payload))
            return {"ok": True, "result": {}}

        telegram_ui._bot_api_post = capture_bot_api
        try:
            await set_bot_commands_for_chat_via_bot_api(
                "123:abc",
                (CommandSpec("menu", "Mostra o menu", "Sistema"),),
                chat_id=456,
            )
            await clear_bot_commands_for_chat_via_bot_api("123:abc", chat_id=456)
        finally:
            telegram_ui._bot_api_post = original

        self.assertEqual(build_chat_command_scope_payload(456), {"type": "chat", "chat_id": 456})
        self.assertEqual(seen[0][1], "setMyCommands")
        self.assertEqual(seen[0][2]["scope"], {"type": "chat", "chat_id": 456})
        self.assertEqual(seen[0][2]["commands"][0]["command"], "menu")
        self.assertEqual(seen[1][2], {"commands": [], "scope": {"type": "chat", "chat_id": 456}})

    async def test_build_command_menu_html_escapes_content(self):
        commands = (
            CommandSpec("menu", "Mostra <menu>", "Interface", usage="/menu <x>"),
        )

        html = build_command_menu_html("Menu & Ajuda", commands, False)

        self.assertIn("<b>Menu &amp; Ajuda</b>", html)
        self.assertIn("<code>/menu &lt;x&gt;</code>", html)
        self.assertIn("Mostra &lt;menu&gt;", html)


if __name__ == "__main__":
    unittest.main()
