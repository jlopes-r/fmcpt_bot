import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from apps.ephemeral_commands.service import BotRuntime, EphemeralCommandService


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class EphemeralCommandServiceTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def build_service() -> EphemeralCommandService:
        service = object.__new__(EphemeralCommandService)
        service.log = Mock()
        service.stop_event = asyncio.Event()
        return service

    async def test_poll_bot_retries_when_initialization_fails(self):
        service = self.build_service()
        runtime = BotRuntime("comandos", "token", (), "Comandos")

        class FakeBotApi:
            initialization_attempts = 0

            def __init__(self, token):
                self.token = token

            async def post(self, session, method, payload):
                if method == "deleteWebhook":
                    self.__class__.initialization_attempts += 1
                    if self.__class__.initialization_attempts == 1:
                        raise ConnectionError("network is not ready")
                if method == "getUpdates":
                    service.stop_event.set()
                    return {"result": []}
                return {"ok": True}

        with (
            patch("apps.ephemeral_commands.service.BotApi", FakeBotApi),
            patch("apps.ephemeral_commands.service.aiohttp.ClientSession", return_value=FakeSession()),
            patch("apps.ephemeral_commands.service.set_bot_commands_via_bot_api", new=AsyncMock()),
            patch("apps.ephemeral_commands.service.set_bot_commands_menu_button_via_bot_api", new=AsyncMock()),
            patch.object(service, "wait_before_retry", new=AsyncMock()) as wait_before_retry,
        ):
            await asyncio.wait_for(service.poll_bot(runtime), timeout=1)

        self.assertEqual(FakeBotApi.initialization_attempts, 2)
        wait_before_retry.assert_awaited_once()
        service.log.exception.assert_called_once()
        self.assertIn("initialization", service.log.exception.call_args.args[1:])

    async def test_run_raises_if_a_listener_stops_unexpectedly(self):
        service = self.build_service()
        service.bots = [BotRuntime("comandos", "token", (), "Comandos")]
        loop = SimpleNamespace(add_signal_handler=Mock())

        with (
            patch("apps.ephemeral_commands.service.asyncio.get_running_loop", return_value=loop),
            patch.object(service, "poll_bot", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            with self.assertRaisesRegex(RuntimeError, "ephemeral-comandos listener stopped unexpectedly") as raised:
                await asyncio.wait_for(service.run(), timeout=1)

        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertTrue(service.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
