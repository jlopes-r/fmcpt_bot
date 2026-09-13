"""Coordenacao assincrona dos jobs e limites persistidos no SQLite."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import socket
import uuid
from typing import Any

from packages.database.repositories import (
    JobClaim,
    JobRecord,
    JobRepository,
    JobStatus,
    RateLimitDecision,
    RateLimitRepository,
)


@dataclass(frozen=True)
class StartupRecovery:
    pending: tuple[JobRecord, ...]
    recovered_ids: tuple[str, ...]
    removed_jobs: int
    removed_rate_events: int


class DurableJobRuntime:
    """Ponte nao bloqueante entre o event loop e os repositorios SQLite."""

    def __init__(
        self,
        jobs: JobRepository,
        rate_limits: RateLimitRepository,
        *,
        worker_id: str | None = None,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self.jobs = jobs
        self.rate_limits = rate_limits
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.heartbeat_interval = max(1.0, float(heartbeat_interval))

    async def submit(
        self,
        *,
        chat_id: int,
        user_id: int,
        url_norm: str,
        platform: str,
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
    ) -> JobClaim:
        return await asyncio.to_thread(
            self.jobs.create_or_get,
            chat_id=chat_id,
            user_id=user_id,
            url_norm=url_norm,
            platform=platform,
            metadata=metadata,
            priority=priority,
            idempotency_window=0,
        )

    async def claim(self, job_id: str) -> JobRecord:
        return await asyncio.to_thread(self.jobs.claim, job_id, self.worker_id)

    async def mark_uploading(self, job_id: str) -> JobRecord:
        current = await asyncio.to_thread(self.jobs.get, job_id)
        if current is None:
            raise LookupError(job_id)
        if current.status is JobStatus.UPLOADING:
            return current
        return await asyncio.to_thread(
            self.jobs.transition,
            job_id,
            JobStatus.UPLOADING,
        )

    async def complete(
        self,
        job_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        return await asyncio.to_thread(
            self.jobs.transition,
            job_id,
            JobStatus.COMPLETED,
            metadata=metadata,
        )

    async def fail(self, job_id: str, error: BaseException) -> JobRecord | None:
        """Finaliza uma falha sem esconder a excecao original do pipeline."""

        try:
            current = await asyncio.to_thread(self.jobs.get, job_id)
            if current is None or current.status in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
            }:
                return current
            return await asyncio.to_thread(
                self.jobs.transition,
                job_id,
                JobStatus.FAILED,
                error=error,
            )
        except Exception:
            return None

    async def allow(
        self,
        subject_key: str,
        *,
        limit: int,
        window_seconds: float,
        scope: str = "telegram_user",
    ) -> RateLimitDecision:
        return await asyncio.to_thread(
            self.rate_limits.consume,
            subject_key,
            limit=limit,
            window_seconds=window_seconds,
            scope=scope,
        )

    async def keep_alive(self, job_id: str) -> None:
        """Atualiza o lease; cancelamento deixa o job recuperavel no reinicio."""

        while True:
            await asyncio.sleep(self.heartbeat_interval)
            alive = await asyncio.to_thread(self.jobs.heartbeat, job_id)
            if not alive:
                return

    async def recover_startup(
        self,
        *,
        job_retention: float = 30 * 86_400,
        rate_event_retention: float = 86_400,
        limit: int = 100,
    ) -> StartupRecovery:
        """Recupera leases do processo anterior e devolve a fila duravel."""

        await asyncio.to_thread(self.jobs.initialize)
        recovered = await asyncio.to_thread(
            self.jobs.recover_interrupted,
            stale_after=0,
        )
        removed_jobs = await asyncio.to_thread(
            self.jobs.cleanup_terminal,
            older_than=job_retention,
        )
        removed_rate_events = await asyncio.to_thread(
            self.rate_limits.cleanup,
            older_than=rate_event_retention,
        )
        pending = await asyncio.to_thread(
            self.jobs.list_jobs,
            status=JobStatus.QUEUED,
            limit=limit,
        )
        pending.sort(key=lambda job: (-job.priority, job.created_at))
        return StartupRecovery(
            pending=tuple(pending),
            recovered_ids=tuple(recovered),
            removed_jobs=removed_jobs,
            removed_rate_events=removed_rate_events,
        )


__all__ = ["DurableJobRuntime", "StartupRecovery"]
