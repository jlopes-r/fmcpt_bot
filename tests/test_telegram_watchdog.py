import asyncio
import logging
import unittest
from types import SimpleNamespace

from pyrogram import raw

from packages.telegram_watchdog import (
    TelegramSessionUnhealthy,
    monitor_pyrogram_client,
    probe_pyrogram_client,
)


class StopMonitor(Exception):
    pass


class FakeClient:
    def __init__(self):
        self.is_connected = True
        self.is_initialized = True
        self.invocations = []
        self.tasks = [
            asyncio.create_task(self._stay_alive())
            for _ in range(4)
        ]
        self.updates_watchdog_task = self.tasks[0]
        self.session = SimpleNamespace(
            recv_task=self.tasks[1],
            ping_task=self.tasks[2],
        )
        self.dispatcher = SimpleNamespace(handler_worker_tasks=[self.tasks[3]])

    @staticmethod
    async def _stay_alive():
        await asyncio.Event().wait()

    async def invoke(self, query):
        self.invocations.append(query)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class TelegramWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.logger = logging.getLogger("test.telegram_watchdog")

    async def asyncTearDown(self):
        await self.client.close()

    async def test_probe_checks_real_updates_rpc(self):
        await probe_pyrogram_client(self.client)

        self.assertEqual(len(self.client.invocations), 1)
        self.assertIsInstance(
            self.client.invocations[0],
            raw.functions.updates.GetState,
        )

    async def test_probe_detects_dead_internal_updates_watchdog(self):
        self.client.updates_watchdog_task.cancel()
        await asyncio.gather(
            self.client.updates_watchdog_task,
            return_exceptions=True,
        )

        with self.assertRaisesRegex(ConnectionError, "updates watchdog was cancelled"):
            await probe_pyrogram_client(self.client)

        self.assertEqual(self.client.invocations, [])

    async def test_monitor_exits_after_consecutive_failures(self):
        attempts = 0
        notifications = []

        async def failing_probe(_client):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("No workers running")

        with self.assertRaisesRegex(
            TelegramSessionUnhealthy,
            "No workers running",
        ):
            await monitor_pyrogram_client(
                self.client,
                logger=self.logger,
                interval_seconds=0.001,
                timeout_seconds=0.1,
                max_failures=3,
                probe=failing_probe,
                notifier=lambda message: notifications.append(message) or True,
            )

        self.assertEqual(attempts, 3)
        self.assertEqual(notifications, [])

    async def test_monitor_notifies_systemd_only_after_success(self):
        notifications = []

        async def successful_probe(_client):
            return None

        def successful_notification(message):
            notifications.append(message)
            raise StopMonitor

        with self.assertRaises(StopMonitor):
            await monitor_pyrogram_client(
                self.client,
                logger=self.logger,
                interval_seconds=60,
                timeout_seconds=0.1,
                probe=successful_probe,
                notifier=successful_notification,
            )

        self.assertEqual(notifications, ["WATCHDOG=1"])


if __name__ == "__main__":
    unittest.main()
