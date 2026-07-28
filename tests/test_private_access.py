import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.private_access import (
    clear_private_access_cache,
    guard_authorized_group_chat,
    guard_private_chat_access,
    user_is_authorized_group_member,
)
from packages import private_access


class FakeClient:
    def __init__(self, members, *, leave_error=None):
        self.members = members
        self.calls = []
        self.left_chats = []
        self.leave_error = leave_error

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        result = self.members.get((chat_id, user_id))
        if isinstance(result, Exception):
            raise result
        return result

    async def leave_chat(self, chat_id):
        if self.leave_error:
            raise self.leave_error
        self.left_chats.append(chat_id)


class FakeMessage:
    def __init__(self, *, chat_type="private", chat_id=10, chat_title="Teste", user_id=42):
        self.chat = SimpleNamespace(type=chat_type, id=chat_id, title=chat_title)
        self.from_user = SimpleNamespace(id=user_id) if user_id else None
        self.replies = []
        self.stopped = False

    async def reply_text(self, text):
        self.replies.append(text)

    def stop_propagation(self):
        self.stopped = True


class PrivateAccessTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_private_access_cache()

    async def test_authorizes_private_user_in_allowed_group(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="member")})

        allowed = await user_is_authorized_group_member(client, 42, [-100])

        self.assertTrue(allowed)

    async def test_denies_user_who_left_all_groups(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="left")})

        allowed = await user_is_authorized_group_member(client, 42, [-100])

        self.assertFalse(allowed)

    async def test_restricted_member_can_still_use_private_chat(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="restricted", is_member=True)})

        allowed = await user_is_authorized_group_member(client, 42, [-100])

        self.assertTrue(allowed)

    async def test_guard_blocks_private_chat_and_stops_handlers(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="left")})
        message = FakeMessage()
        resets = []
        clears = []

        async def clear_commands(token, chat_id):
            clears.append((token, chat_id))
            return True

        async def reset_button(token, chat_id=None):
            resets.append((token, chat_id))
            return True

        original = private_access.set_bot_commands_menu_button_via_bot_api
        original_clear = private_access.clear_bot_commands_for_chat_via_bot_api
        private_access.set_bot_commands_menu_button_via_bot_api = reset_button
        private_access.clear_bot_commands_for_chat_via_bot_api = clear_commands

        try:
            with self.assertLogs("PrivateAccess", level="WARNING") as logs:
                allowed = await guard_private_chat_access(
                    client,
                    message,
                    [-100],
                    bot_label="Teste Bot",
                    bot_token="123:abc",
                    mini_app_url="https://example.com",
                )
        finally:
            private_access.set_bot_commands_menu_button_via_bot_api = original
            private_access.clear_bot_commands_for_chat_via_bot_api = original_clear

        self.assertFalse(allowed)
        self.assertTrue(message.stopped)
        self.assertEqual(message.replies, [])
        self.assertEqual(clears, [("123:abc", 42)])
        self.assertEqual(resets, [("123:abc", 42)])
        self.assertIn("unauthorized private chat blocked", logs.output[0])
        self.assertIn("Teste Bot", logs.output[0])

    async def test_guard_sets_private_web_app_button_for_authorized_user(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="member")})
        message = FakeMessage()
        webapps = []
        chat_commands = []

        async def set_webapp(token, url, label="Painel", chat_id=None):
            webapps.append((token, url, label, chat_id))
            return True

        async def set_commands(token, commands, chat_id):
            chat_commands.append((token, commands, chat_id))
            return True

        original = private_access.set_bot_menu_button_via_bot_api
        original_commands = private_access.set_bot_commands_for_chat_via_bot_api
        private_access.set_bot_menu_button_via_bot_api = set_webapp
        private_access.set_bot_commands_for_chat_via_bot_api = set_commands
        try:
            allowed = await guard_private_chat_access(
                client,
                message,
                [-100],
                bot_token="123:abc",
                mini_app_url="https://example.com",
                bot_commands=("menu",),
            )
        finally:
            private_access.set_bot_menu_button_via_bot_api = original
            private_access.set_bot_commands_for_chat_via_bot_api = original_commands

        self.assertTrue(allowed)
        self.assertFalse(message.stopped)
        self.assertEqual(chat_commands, [("123:abc", ("menu",), 42)])
        self.assertEqual(webapps, [("123:abc", "https://example.com", "Painel", 42)])

    async def test_group_chat_bypasses_private_membership_guard(self):
        client = FakeClient({})
        message = FakeMessage(chat_type="group")

        allowed = await guard_private_chat_access(client, message, [-100])

        self.assertTrue(allowed)
        self.assertFalse(message.stopped)
        self.assertEqual(client.calls, [])

    async def test_membership_result_is_cached(self):
        client = FakeClient({(-100, 42): SimpleNamespace(status="member")})

        self.assertTrue(await user_is_authorized_group_member(client, 42, [-100]))
        self.assertTrue(await user_is_authorized_group_member(client, 42, [-100]))

        self.assertEqual(client.calls, [(-100, 42)])

    async def test_authorized_group_chat_is_allowed(self):
        client = FakeClient({})
        message = FakeMessage(chat_type="supergroup", chat_id=-100)

        allowed = await guard_authorized_group_chat(client, message, [-100], bot_label="Teste Bot")

        self.assertTrue(allowed)
        self.assertFalse(message.stopped)
        self.assertEqual(client.left_chats, [])

    async def test_unauthorized_group_chat_is_logged_and_left(self):
        client = FakeClient({})
        message = FakeMessage(chat_type="supergroup", chat_id=-200, chat_title="Grupo errado")

        with self.assertLogs("PrivateAccess", level="WARNING") as logs:
            allowed = await guard_authorized_group_chat(client, message, [-100], bot_label="Teste Bot")

        self.assertFalse(allowed)
        self.assertTrue(message.stopped)
        self.assertEqual(client.left_chats, [-200])
        self.assertIn("unauthorized group chat blocked", logs.output[0])
        self.assertIn("Grupo errado", logs.output[0])

    async def test_unauthorized_group_stops_even_if_leave_fails(self):
        client = FakeClient({}, leave_error=RuntimeError("leave failed"))
        message = FakeMessage(chat_type="group", chat_id=-200)

        with self.assertLogs("PrivateAccess", level="WARNING"):
            allowed = await guard_authorized_group_chat(client, message, [-100])

        self.assertFalse(allowed)
        self.assertTrue(message.stopped)


if __name__ == "__main__":
    unittest.main()
