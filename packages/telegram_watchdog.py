"""Health supervision for long-running Pyrogram clients.

Pyrogram's own updates watchdog can terminate after an unhandled Telegram
server error while the main process keeps running.  In that state systemd sees
an active service, but the bot no longer receives commands.  This module
checks both Pyrogram's internal workers and a real updates RPC so the service
can fail fast and be restarted by its supervisor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from pyrogram import raw


LOCAL_HEARTBEAT_INTERVAL_SECONDS = 30.0
REMOTE_PROBE_INTERVAL_SECONDS = 8 * 60 * 60.0


class TelegramSessionUnhealthy(RuntimeError):
    """Raised when a Pyrogram client remains unhealthy across several probes."""


def _task_problem(task: asyncio.Task[Any] | None, label: str) -> str | None:
    if task is None:
        return f"{label} was not created"
    if not task.done():
        return None
    if task.cancelled():
        return f"{label} was cancelled"

    exception = task.exception()
    if exception is None:
        return f"{label} stopped unexpectedly"
    return f"{label} stopped with {type(exception).__name__}: {exception}"


def inspect_pyrogram_workers(client: Any) -> None:
    """Check Pyrogram's local tasks without making a Telegram request."""

    if not getattr(client, "is_connected", False):
        raise ConnectionError("Pyrogram client is disconnected")
    if not getattr(client, "is_initialized", False):
        raise ConnectionError("Pyrogram client is not initialized")

    tasks: list[tuple[asyncio.Task[Any] | None, str]] = [
        (getattr(client, "updates_watchdog_task", None), "updates watchdog"),
    ]

    session = getattr(client, "session", None)
    if session is None:
        raise ConnectionError("Pyrogram session is missing")
    tasks.extend(
        (
            (getattr(session, "recv_task", None), "session receive worker"),
            (getattr(session, "ping_task", None), "session ping worker"),
        )
    )

    dispatcher = getattr(client, "dispatcher", None)
    handler_tasks = getattr(dispatcher, "handler_worker_tasks", None)
    if not handler_tasks:
        raise ConnectionError("Pyrogram dispatcher has no handler workers")
    tasks.extend(
        (task, f"handler worker {index}")
        for index, task in enumerate(handler_tasks, start=1)
    )

    for task, label in tasks:
        problem = _task_problem(task, label)
        if problem:
            raise ConnectionError(problem)


async def probe_pyrogram_client(client: Any) -> None:
    """Verify local workers and perform the updates RPC that failed in production."""

    inspect_pyrogram_workers(client)
    await client.invoke(raw.functions.updates.GetState())
    inspect_pyrogram_workers(client)


def notify_systemd(message: str = "WATCHDOG=1") -> bool:
    """Send an sd_notify datagram without adding a systemd Python dependency."""

    notify_socket = os.getenv("NOTIFY_SOCKET")
    if not notify_socket:
        return False

    address = notify_socket
    if address.startswith("@"):
        address = "\0" + address[1:]

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.settimeout(1.0)
            notifier.sendto(message.encode("utf-8"), address)
    except OSError:
        return False
    return True


async def monitor_pyrogram_client(
    client: Any,
    *,
    logger: logging.Logger,
    interval_seconds: float = LOCAL_HEARTBEAT_INTERVAL_SECONDS,
    remote_probe_interval_seconds: float = REMOTE_PROBE_INTERVAL_SECONDS,
    timeout_seconds: float = 15.0,
    max_failures: int = 3,
    probe: Callable[[Any], Awaitable[None]] = probe_pyrogram_client,
    local_probe: Callable[[Any], None] = inspect_pyrogram_workers,
    notifier: Callable[[str], bool] = notify_systemd,
    on_unhealthy: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Continuously supervise a client, raising after persistent failures.

    Local worker checks feed systemd without generating Telegram traffic.  A
    real ``updates.GetState`` request runs only three times per day by default.
    Failed checks do not send a heartbeat, so systemd can still recover the
    service if this coroutine or the whole event loop becomes stuck.
    """

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if remote_probe_interval_seconds <= 0:
        raise ValueError("remote_probe_interval_seconds must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_failures <= 0:
        raise ValueError("max_failures must be positive")

    failures = 0
    last_error: BaseException | None = None
    next_remote_probe = 0.0
    loop = asyncio.get_running_loop()

    while True:
        remote_probe_due = loop.time() >= next_remote_probe
        probe_kind = "remote" if remote_probe_due else "local"
        try:
            local_probe(client)
            if remote_probe_due:
                await asyncio.wait_for(probe(client), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            last_error = exc
            logger.warning(
                "Telegram watchdog %s probe failed (%s/%s): %s",
                probe_kind,
                failures,
                max_failures,
                exc,
            )
            if failures >= max_failures:
                reason = (
                    "Telegram session remained unhealthy after "
                    f"{max_failures} probes: {last_error}"
                )
                if on_unhealthy is not None:
                    try:
                        await asyncio.wait_for(
                            on_unhealthy(reason),
                            timeout=min(timeout_seconds, 10.0),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as alert_error:
                        logger.error(
                            "Could not send Telegram watchdog alert: %s",
                            alert_error,
                        )
                raise TelegramSessionUnhealthy(reason) from exc
        else:
            if failures:
                logger.info(
                    "Telegram session recovered after %s failed probe(s).",
                    failures,
                )
            failures = 0
            last_error = None
            if remote_probe_due:
                next_remote_probe = loop.time() + remote_probe_interval_seconds
                logger.info(
                    "Telegram remote probe succeeded; next check in %.1f hours.",
                    remote_probe_interval_seconds / 3600,
                )
            notifier("WATCHDOG=1")

        await asyncio.sleep(interval_seconds)
