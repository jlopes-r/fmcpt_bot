import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class AdminCommandsTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.super_bot = importlib.import_module("apps.telegram_bot.super_bot")

    @classmethod
    def tearDownClass(cls):
        del cls.super_bot
        sys.modules.pop("apps.telegram_bot.super_bot", None)

    @staticmethod
    def _message(user_id=None):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            reply_text=AsyncMock(),
        )
        if user_id is not None:
            message.from_user = SimpleNamespace(id=user_id)
        return message

    async def test_sync_denies_non_admin_without_running_handler(self):
        super_bot = self.super_bot
        message = self._message(user_id=222)

        with (
            patch.object(super_bot, "ADMIN_ID", 111),
            patch.object(super_bot, "chat_autorizado", return_value=True),
            patch.object(
                super_bot,
                "atualizar_menu_comandos_super",
                new=AsyncMock(),
            ) as update_menu,
        ):
            result = await super_bot.cmd_sync(None, message)

        self.assertIsNone(result)
        update_menu.assert_not_awaited()
        message.reply_text.assert_awaited_once()
        self.assertIn("Acesso negado", message.reply_text.await_args.args[0])

    async def test_admin_can_run_sync(self):
        super_bot = self.super_bot
        message = self._message(user_id=111)

        with (
            patch.object(super_bot, "ADMIN_ID", 111),
            patch.object(super_bot, "chat_autorizado", return_value=True),
            patch.object(
                super_bot,
                "atualizar_menu_comandos_super",
                new=AsyncMock(return_value=True),
            ) as update_menu,
        ):
            await super_bot.cmd_sync(None, message)

        update_menu.assert_awaited_once()
        self.assertIn("atualizado", message.reply_text.await_args.args[0])

    async def test_missing_sender_is_denied_before_instagram_status(self):
        super_bot = self.super_bot
        message = self._message()

        with (
            patch.object(super_bot, "ADMIN_ID", 111),
            patch.object(super_bot, "inspect_cookie_health") as inspect,
        ):
            await super_bot.cmd_ig_status(None, message)

        inspect.assert_not_called()
        message.reply_text.assert_awaited_once()
        self.assertIn("Acesso negado", message.reply_text.await_args.args[0])

    async def test_unconfigured_admin_denies_instagram_renew(self):
        super_bot = self.super_bot
        message = self._message(user_id=111)

        with (
            patch.object(super_bot, "ADMIN_ID", 0),
            patch.object(super_bot, "_auto_login_and_save_cookies") as renew,
        ):
            await super_bot.cmd_ig_renew(None, message)

        renew.assert_not_called()
        message.reply_text.assert_awaited_once()
        self.assertIn("Acesso negado", message.reply_text.await_args.args[0])

    def test_catalog_has_only_protected_instagram_admin_commands(self):
        super_bot = self.super_bot
        commands = {command.name: command for command in super_bot.SUPER_COMMANDS}

        self.assertNotIn("update_ytdlp", commands)
        for name in ("sync", "ig_status", "ig_renew"):
            self.assertTrue(commands[name].admin_only)

        catalog_path = Path(__file__).resolve().parents[1] / "apps" / "mini_app" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        super_catalog = {
            command["name"]: command
            for bot in catalog["bots"]
            if bot["id"] == "super"
            for command in bot["commands"]
        }
        self.assertNotIn("update_ytdlp", super_catalog)
        for name in ("sync", "ig_status", "ig_renew"):
            self.assertTrue(super_catalog[name]["adminOnly"])

    def test_ytdlp_updaters_are_not_exposed(self):
        super_bot = self.super_bot

        self.assertFalse(hasattr(super_bot, "cmd_update_ytdlp"))
        self.assertFalse(hasattr(super_bot, "_atualizar_ytdlp_sync"))
        self.assertFalse(hasattr(super_bot, "_atualizar_ytdlp_async"))
        self.assertFalse(hasattr(super_bot, "limpeza_update_ytdlp_periodica"))


if __name__ == "__main__":
    unittest.main()
