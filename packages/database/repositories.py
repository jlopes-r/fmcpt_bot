"""Repositorios transacionais para fila, limites e metricas."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from packages.database import database_manager
from packages.observability import redact_sensitive


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.UPLOADING,
)
TERMINAL_JOB_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED)

_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.DOWNLOADING, JobStatus.FAILED}),
    JobStatus.DOWNLOADING: frozenset(
        {JobStatus.QUEUED, JobStatus.UPLOADING, JobStatus.FAILED}
    ),
    JobStatus.UPLOADING: frozenset(
        {JobStatus.QUEUED, JobStatus.COMPLETED, JobStatus.FAILED}
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
}


class JobNotFound(LookupError):
    pass


class InvalidJobTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    idempotency_key: str
    chat_id: int
    user_id: int
    url_norm: str
    platform: str
    status: JobStatus
    priority: int
    worker_id: str | None
    attempt_count: int
    created_at: float
    updated_at: float
    started_at: float | None
    completed_at: float | None
    error_type: str | None
    error_message: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class JobClaim:
    job: JobRecord
    reused: bool
    reason: str = "created"


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: float
    used: int


def _safe_json(value: dict[str, Any] | None) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata/labels precisam ser serializaveis em JSON") from exc


def _decode_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        idempotency_key=str(row["idempotency_key"]),
        chat_id=int(row["chat_id"]),
        user_id=int(row["user_id"]),
        url_norm=str(row["url_norm"]),
        platform=str(row["platform"]),
        status=JobStatus(str(row["status"])),
        priority=int(row["priority"]),
        worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
        attempt_count=int(row["attempt_count"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=float(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=(
            float(row["completed_at"]) if row["completed_at"] is not None else None
        ),
        error_type=str(row["error_type"]) if row["error_type"] is not None else None,
        error_message=(
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        metadata=_decode_json(row["metadata_json"]),
    )


class _Repository:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = str(Path(db_path).resolve()) if db_path is not None else None

    def initialize(self) -> None:
        database_manager.init_db(self.db_path)

    def _connection(self, *, write: bool = False):
        return database_manager.connection(self.db_path, write=write)


class JobRepository(_Repository):
    """Fila duravel de downloads, segura para mais de um worker."""

    @staticmethod
    def idempotency_key(chat_id: int, url_norm: str) -> str:
        raw = f"{int(chat_id)}\0{url_norm.strip()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def create_or_get(
        self,
        *,
        chat_id: int,
        user_id: int,
        url_norm: str,
        platform: str = "",
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        idempotency_window: float = 3_600,
        now: float | None = None,
        job_id: str | None = None,
    ) -> JobClaim:
        """Cria um job ou devolve o equivalente ainda ativo/recentes.

        A transacao IMMEDIATE e o indice parcial garantem idempotencia mesmo
        quando duas atualizacoes do mesmo chat chegam simultaneamente.
        """

        if not url_norm or not url_norm.strip():
            raise ValueError("url_norm nao pode ser vazia")
        timestamp = float(time.time() if now is None else now)
        key = self.idempotency_key(chat_id, url_norm)
        with self._connection(write=True) as conn:
            active = conn.execute(
                """
                SELECT * FROM jobs
                WHERE idempotency_key = ?
                  AND status IN ('queued', 'downloading', 'uploading')
                ORDER BY created_at DESC LIMIT 1
                """,
                (key,),
            ).fetchone()
            if active:
                return JobClaim(_job_from_row(active), True, "active")

            if idempotency_window > 0:
                recent = conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE idempotency_key = ? AND status = 'completed'
                      AND completed_at >= ?
                    ORDER BY completed_at DESC LIMIT 1
                    """,
                    (key, timestamp - idempotency_window),
                ).fetchone()
                if recent:
                    return JobClaim(_job_from_row(recent), True, "recently_completed")

            generated_id = job_id or uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, idempotency_key, chat_id, user_id, url_norm,
                    platform, status, priority, created_at, updated_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    generated_id,
                    key,
                    int(chat_id),
                    int(user_id),
                    url_norm.strip(),
                    platform.strip().lower(),
                    int(priority),
                    timestamp,
                    timestamp,
                    _safe_json(metadata),
                ),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (generated_id,)
            ).fetchone()
            assert row is not None
            return JobClaim(_job_from_row(row), False)

    def get(self, job_id: str) -> JobRecord | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job_from_row(row) if row else None

    def list_jobs(
        self,
        *,
        status: JobStatus | str | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(JobStatus(status).value)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(int(user_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1_000)))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def claim_next(
        self,
        worker_id: str,
        *,
        platforms: tuple[str, ...] = (),
        now: float | None = None,
    ) -> JobRecord | None:
        timestamp = float(time.time() if now is None else now)
        with self._connection(write=True) as conn:
            params: list[object] = []
            platform_filter = ""
            if platforms:
                cleaned = tuple(value.strip().lower() for value in platforms)
                placeholders = ",".join("?" for _ in cleaned)
                platform_filter = f" AND platform IN ({placeholders})"
                params.extend(cleaned)
            row = conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE status = 'queued'{platform_filter}
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,  # noqa: S608
                params,
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status = 'downloading', worker_id = ?, updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    attempt_count = attempt_count + 1,
                    error_type = NULL, error_message = NULL
                WHERE job_id = ? AND status = 'queued'
                """,
                (worker_id, timestamp, timestamp, row["job_id"]),
            )
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            assert claimed is not None
            return _job_from_row(claimed)

    def claim(self, job_id: str, worker_id: str, *, now: float | None = None) -> JobRecord:
        """Reivindica um job especifico sem correr o risco de pegar outra URL."""
        timestamp = float(time.time() if now is None else now)
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'downloading', worker_id = ?, updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    attempt_count = attempt_count + 1,
                    error_type = NULL, error_message = NULL
                WHERE job_id = ? AND status = 'queued'
                """,
                (worker_id, timestamp, timestamp, job_id),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise JobNotFound(job_id)
                raise InvalidJobTransition(
                    f"job {job_id} nao esta queued: {row['status']}"
                )
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            assert claimed is not None
            return _job_from_row(claimed)

    def transition(
        self,
        job_id: str,
        status: JobStatus | str,
        *,
        error: BaseException | None = None,
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> JobRecord:
        target = JobStatus(status)
        timestamp = float(time.time() if now is None else now)
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise JobNotFound(job_id)
            current = JobStatus(str(row["status"]))
            if target != current and target not in _ALLOWED_TRANSITIONS[current]:
                raise InvalidJobTransition(f"transicao invalida: {current.value} -> {target.value}")

            metadata_json = row["metadata_json"]
            if metadata is not None:
                merged = _decode_json(metadata_json)
                merged.update(metadata)
                metadata_json = _safe_json(merged)
            error_type = type(error).__name__ if error is not None else None
            error_message = redact_sensitive(error)[:2_000] if error is not None else None
            completed_at = timestamp if target in TERMINAL_JOB_STATUSES else None
            worker_id = None if target in TERMINAL_JOB_STATUSES or target is JobStatus.QUEUED else row["worker_id"]
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, completed_at = ?, worker_id = ?,
                    error_type = ?, error_message = ?, metadata_json = ?
                WHERE job_id = ?
                """,
                (
                    target.value,
                    timestamp,
                    completed_at,
                    worker_id,
                    error_type,
                    error_message,
                    metadata_json,
                    job_id,
                ),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            assert updated is not None
            return _job_from_row(updated)

    def heartbeat(self, job_id: str, *, now: float | None = None) -> bool:
        timestamp = float(time.time() if now is None else now)
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET updated_at = ?
                WHERE job_id = ? AND status IN ('downloading', 'uploading')
                """,
                (timestamp, job_id),
            )
            return cursor.rowcount == 1

    def recover_interrupted(
        self,
        *,
        stale_after: float = 900,
        now: float | None = None,
    ) -> list[str]:
        timestamp = float(time.time() if now is None else now)
        cutoff = timestamp - max(0, float(stale_after))
        with self._connection(write=True) as conn:
            rows = conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status IN ('downloading', 'uploading') AND updated_at <= ?
                ORDER BY updated_at
                """,
                (cutoff,),
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in rows]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                conn.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'queued', worker_id = NULL, updated_at = ?,
                        completed_at = NULL, error_type = 'WorkerInterrupted',
                        error_message = 'job recuperado apos interrupcao do worker'
                    WHERE job_id IN ({placeholders})
                    """,  # noqa: S608
                    (timestamp, *job_ids),
                )
            return job_ids

    def cleanup_terminal(
        self,
        *,
        older_than: float = 30 * 86_400,
        now: float | None = None,
    ) -> int:
        timestamp = float(time.time() if now is None else now)
        cutoff = timestamp - max(0, float(older_than))
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                DELETE FROM jobs
                WHERE status IN ('completed', 'failed')
                  AND COALESCE(completed_at, updated_at) < ?
                """,
                (cutoff,),
            )
            return max(0, cursor.rowcount)

    def queue_depth(self) -> dict[str, int]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM jobs GROUP BY status"
            ).fetchall()
        result = {status.value: 0 for status in JobStatus}
        result.update({str(row["status"]): int(row["total"]) for row in rows})
        return result


class RateLimitRepository(_Repository):
    """Janela deslizante persistente compartilhada por todos os processos."""

    def consume(
        self,
        subject_key: str,
        *,
        limit: int,
        window_seconds: float,
        cost: int = 1,
        scope: str = "user",
        now: float | None = None,
    ) -> RateLimitDecision:
        if limit <= 0 or cost <= 0 or window_seconds <= 0:
            raise ValueError("limit, cost e window_seconds precisam ser positivos")
        timestamp = float(time.time() if now is None else now)
        cutoff = timestamp - float(window_seconds)
        with self._connection(write=True) as conn:
            conn.execute(
                """
                DELETE FROM rate_limit_events
                WHERE scope = ? AND subject_key = ? AND occurred_at <= ?
                """,
                (scope, subject_key, cutoff),
            )
            rows = conn.execute(
                """
                SELECT cost, occurred_at FROM rate_limit_events
                WHERE scope = ? AND subject_key = ? AND occurred_at > ?
                ORDER BY occurred_at ASC, event_id ASC
                """,
                (scope, subject_key, cutoff),
            ).fetchall()
            used = sum(int(row["cost"]) for row in rows)
            if used + cost <= limit:
                conn.execute(
                    """
                    INSERT INTO rate_limit_events (scope, subject_key, cost, occurred_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (scope, subject_key, cost, timestamp),
                )
                return RateLimitDecision(True, max(0, limit - used - cost), 0.0, used + cost)

            required = used + cost - limit
            released = 0
            retry_at = timestamp + window_seconds
            for row in rows:
                released += int(row["cost"])
                retry_at = float(row["occurred_at"]) + window_seconds
                if released >= required:
                    break
            return RateLimitDecision(False, max(0, limit - used), max(0.0, retry_at - timestamp), used)

    def reset(self, subject_key: str, *, scope: str = "user") -> int:
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM rate_limit_events WHERE scope = ? AND subject_key = ?",
                (scope, subject_key),
            )
            return max(0, cursor.rowcount)


class MetricsRepository(_Repository):
    """Armazena eventos de observabilidade para sobreviver a reinicios."""

    def record(
        self,
        name: str,
        *,
        kind: str = "counter",
        value: float = 1.0,
        platform: str = "",
        stage: str = "",
        account: str = "",
        status: str = "",
        status_code: int | None = None,
        job_id: str | None = None,
        labels: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        if kind not in {"counter", "gauge", "timing"}:
            raise ValueError("kind deve ser counter, gauge ou timing")
        if not name or not name.strip():
            raise ValueError("nome da metrica nao pode ser vazio")
        recorded_at = float(time.time() if timestamp is None else timestamp)
        with self._connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO metric_events (
                    name, kind, value, platform, stage, account, status,
                    status_code, job_id, labels_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    kind,
                    float(value),
                    platform.strip().lower(),
                    stage.strip().lower(),
                    account.strip(),
                    status.strip().lower(),
                    status_code,
                    job_id,
                    _safe_json(labels),
                    recorded_at,
                ),
            )

    def summary(
        self,
        *,
        since: float = 0,
        name: str | None = None,
    ) -> list[dict[str, object]]:
        params: list[object] = [float(since)]
        name_filter = ""
        if name is not None:
            name_filter = " AND name = ?"
            params.append(name)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT name, kind, platform, stage, status, status_code,
                       COUNT(*) AS samples, SUM(value) AS total,
                       AVG(value) AS average, MAX(timestamp) AS last_seen
                FROM metric_events
                WHERE timestamp >= ?{name_filter}
                GROUP BY name, kind, platform, stage, status, status_code
                ORDER BY name, platform, stage, status
                """,  # noqa: S608
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_gauge(
        self,
        name: str,
        *,
        platform: str = "",
        account: str = "",
    ) -> float | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT value FROM metric_events
                WHERE name = ? AND kind = 'gauge' AND platform = ? AND account = ?
                ORDER BY timestamp DESC, metric_id DESC LIMIT 1
                """,
                (name, platform.strip().lower(), account.strip()),
            ).fetchone()
        return float(row["value"]) if row else None

    def cleanup(self, *, older_than: float, now: float | None = None) -> int:
        timestamp = float(time.time() if now is None else now)
        cutoff = timestamp - max(0, float(older_than))
        with self._connection(write=True) as conn:
            cursor = conn.execute("DELETE FROM metric_events WHERE timestamp < ?", (cutoff,))
            return max(0, cursor.rowcount)


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "InvalidJobTransition",
    "JobClaim",
    "JobNotFound",
    "JobRecord",
    "JobRepository",
    "JobStatus",
    "MetricsRepository",
    "RateLimitDecision",
    "RateLimitRepository",
    "TERMINAL_JOB_STATUSES",
]
