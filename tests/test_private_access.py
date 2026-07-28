import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.private_access import (
    clear_private_access_cache,
    guard_private_chat_access,
    user_is_authorized_group_member,
)


class FakeClient:
    def __init__(self, members):
        self.members = members
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        result = self.members.get((chat_id, user_id))
        if isinstance(result, Exception):
            raise result
        return result


class FakeMessage:
    def __init__(self, *, chat_type="private", user_id=42):
        self.chat = SimpleNamespace(type=chat_type)
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

        allowed = await guard_private_chat_access(client, message, [-100])

        self.assertFalse(allowed)
        self.assertTrue(message.stopped)
        self.assertIn("Acesso restrito", message.replies[0])

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


if __name__ == "__main__":
    unittest.main()
