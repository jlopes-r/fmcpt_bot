import os
import unittest
from unittest.mock import patch

from packages.config import get_int_env, parse_chat_ids


class ConfigTest(unittest.TestCase):
    def test_get_int_env_uses_default_when_value_is_invalid(self):
        with patch.dict(os.environ, {"BOT_TEST_NUMBER": "invalid"}, clear=False):
            self.assertEqual(get_int_env("BOT_TEST_NUMBER", 42), 42)

    def test_parse_chat_ids_ignores_invalid_entries(self):
        self.assertEqual(parse_chat_ids("-1001, invalid, 42, , 7x"), [-1001, 42])


if __name__ == "__main__":
    unittest.main()
