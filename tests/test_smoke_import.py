import importlib
import os
import asyncio
import logging
import unittest
from unittest.mock import patch


class SmokeImportTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        pending = [task for task in asyncio.all_tasks(self.loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)
            handler.close()
        asyncio.set_event_loop(None)
        self.loop.close()

    def test_import_super_bot_sem_token_real(self):
        env = {
            **os.environ,
            "API_ID": "12345",
            "API_HASH": "hash_fake",
            "BOT_TOKEN": "12345:fake",
        }
        with patch.dict(os.environ, env, clear=False):
            module = importlib.import_module("apps.telegram_bot.super_bot")
            self.assertEqual(module.API_ID, 12345)

    def test_import_comandos_bot_sem_token_real(self):
        env = {
            **os.environ,
            "API_ID": "12345",
            "API_HASH": "hash_fake",
            "BOT_TOKEN_COMANDOS": "12345:fake",
        }
        with patch.dict(os.environ, env, clear=False):
            module = importlib.import_module("apps.comandos.comandos_bot")
            self.assertEqual(module.API_ID, 12345)


if __name__ == "__main__":
    unittest.main()
