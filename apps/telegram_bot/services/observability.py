"""Observabilidade do pipeline social com metricas duraveis."""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from apps.telegram_bot.errors import SocialMediaError
from packages.database.repositories import JobRepository, MetricsRepository
from packages.observability import (
    ContextLoggerAdapter,
    bind_log_context,
    get_log_context,
    get_logger,
    redact_sensitive,
)


class PipelineObserver:
    """Registra logs e metricas sem acoplar extratores ao SQLite."""

    def __init__(
        self,
        metrics: MetricsRepository,
        *,
        jobs: JobRepository | None = None,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self.metrics = metrics
        self.jobs = jobs
        self.log = logger or get_logger("social_pipeline")

    def _record_metric(self, name: str, **fields: Any) -> None:
        try:
            self.metrics.record(name, **fields)
        except (OSError, sqlite3.DatabaseError, ValueError):
            self.log.warning(
                "falha ao persistir metrica",
                exc_info=True,
                extra={"event": "metric_write_failed", "stage": "observability"},
            )

    @contextmanager
    def job_scope(
        self,
        job_id: str,
        *,
        platform: str = "",
        account: str = "",
    ) -> Iterator[None]:
        with bind_log_context(job_id=job_id, platform=platform, account=account):
            yield

    @contextmanager
    def stage(
        self,
        stage: str,
        *,
        platform: str | None = None,
        account: str | None = None,
    ) -> Iterator[None]:
        context = get_log_context()
        resolved_platform = platform or str(context.get("platform", ""))
        resolved_account = account or str(context.get("account", ""))
        job_id = str(context.get("job_id", "")) or None
        started = time.perf_counter()
        with bind_log_context(
            stage=stage,
            platform=resolved_platform,
            account=resolved_account,
        ):
            self.log.info(
                "etapa iniciada",
                extra={"event": "stage_started", "status": "started"},
            )
            try:
                yield
            except BaseException as exc:
                elapsed_ms = (time.perf_counter() - started) * 1_000
                self._record_metric(
                    "pipeline_stage_duration_ms",
                    kind="timing",
                    value=elapsed_ms,
                    platform=resolved_platform,
                    stage=stage,
                    account=resolved_account,
                    status="failed",
                    job_id=job_id,
                )
                self.record_error(
                    exc,
                    platform=resolved_platform,
                    stage=stage,
                    account=resolved_account,
                    job_id=job_id,
                    duration_ms=elapsed_ms,
                )
                raise
            else:
                elapsed_ms = (time.perf_counter() - started) * 1_000
                self._record_metric(
                    "pipeline_stage_duration_ms",
                    kind="timing",
                    value=elapsed_ms,
                    platform=resolved_platform,
                    stage=stage,
                    account=resolved_account,
                    status="success",
                    job_id=job_id,
                )
                self.log.info(
                    "etapa concluida",
                    extra={
                        "event": "stage_completed",
                        "status": "success",
                        "duration_ms": round(elapsed_ms, 3),
                    },
                )

    def record_job(self, status: str, *, platform: str, job_id: str | None = None) -> None:
        self._record_metric(
            "pipeline_jobs_total",
            kind="counter",
            platform=platform,
            status=status,
            job_id=job_id,
        )
        self.log.info(
            "estado do job alterado",
            extra={"event": "job_status", "status": status, "job_id": job_id},
        )

    def record_error(
        self,
        error: BaseException,
        *,
        platform: str = "",
        stage: str = "",
        account: str = "",
        job_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "event": "pipeline_error",
            "platform": platform,
            "stage": stage,
            "account": account,
            "job_id": job_id,
            "status": "failed",
            "error_type": type(error).__name__,
        }
        status_code: int | None = None
        error_code = type(error).__name__
        if isinstance(error, SocialMediaError):
            fields.update(error.log_fields())
            status_code = error.status_code
            error_code = error.error_code
        if duration_ms is not None:
            fields["duration_ms"] = round(duration_ms, 3)
        self._record_metric(
            "pipeline_errors_total",
            kind="counter",
            platform=platform,
            stage=stage,
            account=account,
            status=error_code,
            status_code=status_code,
            job_id=job_id,
        )
        self.log.error(redact_sensitive(error), extra=fields)

    def record_fallback(
        self,
        name: str,
        *,
        platform: str,
        stage: str = "extract",
        job_id: str | None = None,
    ) -> None:
        self._record_metric(
            "pipeline_fallback_total",
            kind="counter",
            platform=platform,
            stage=stage,
            status=name,
            job_id=job_id,
        )
        self.log.warning(
            "fallback acionado",
            extra={
                "event": "fallback",
                "fallback": name,
                "platform": platform,
                "stage": stage,
                "job_id": job_id,
            },
        )

    def record_http(
        self,
        status_code: int,
        *,
        platform: str,
        stage: str = "extract",
        account: str = "",
        job_id: str | None = None,
    ) -> None:
        self._record_metric(
            "social_http_responses_total",
            kind="counter",
            platform=platform,
            stage=stage,
            account=account,
            status=str(status_code),
            status_code=int(status_code),
            job_id=job_id,
        )

    def record_cookie_health(
        self,
        account: str,
        status: str,
        *,
        platform: str = "instagram",
    ) -> None:
        value = 1.0 if status.strip().lower() == "healthy" else 0.0
        self._record_metric(
            "social_cookie_healthy",
            kind="gauge",
            value=value,
            platform=platform,
            account=account,
            status=status,
        )

    def record_runtime_health(self, storage_path: str | Path) -> dict[str, int]:
        usage = shutil.disk_usage(Path(storage_path))
        values = {
            "disk_free_bytes": int(usage.free),
            "disk_used_bytes": int(usage.used),
            "disk_total_bytes": int(usage.total),
        }
        for name, value in values.items():
            self._record_metric(name, kind="gauge", value=value)
        if self.jobs is not None:
            for status, depth in self.jobs.queue_depth().items():
                self._record_metric(
                    "pipeline_queue_depth",
                    kind="gauge",
                    value=depth,
                    status=status,
                )
        return values

    def startup_maintenance(
        self,
        *,
        stale_after: float = 900,
        job_retention: float = 30 * 86_400,
        metric_retention: float = 30 * 86_400,
        now: float | None = None,
    ) -> dict[str, int]:
        if self.jobs is None:
            raise RuntimeError("JobRepository necessario para manutencao")
        recovered = self.jobs.recover_interrupted(stale_after=stale_after, now=now)
        removed_jobs = self.jobs.cleanup_terminal(older_than=job_retention, now=now)
        removed_metrics = self.metrics.cleanup(older_than=metric_retention, now=now)
        result = {
            "recovered_jobs": len(recovered),
            "removed_jobs": removed_jobs,
            "removed_metrics": removed_metrics,
        }
        self.log.info(
            "manutencao de persistencia concluida",
            extra={"event": "persistence_maintenance", "status": "success"},
        )
        return result


__all__ = ["PipelineObserver"]
