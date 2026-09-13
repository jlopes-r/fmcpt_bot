import io
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from apps.telegram_bot.errors import RateLimited
from apps.telegram_bot.services.observability import PipelineObserver
from packages.database import database_manager
from packages.database.repositories import (
    InvalidJobTransition,
    JobRepository,
    JobStatus,
    MetricsRepository,
    RateLimitRepository,
)
from packages.observability import (
    StructuredJsonFormatter,
    bind_log_context,
    get_logger,
    redact_sensitive,
)


class PersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "pipeline.db")
        database_manager.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()


class DatabaseInfrastructureTest(PersistenceTestCase):
    def test_wal_busy_timeout_and_operational_indexes(self):
        with database_manager.connection(self.db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }

        self.assertEqual(mode.lower(), "wal")
        self.assertGreaterEqual(timeout, 1_000)
        self.assertIn("idx_jobs_timestamp", indexes)
        self.assertIn("idx_jobs_user_timestamp", indexes)
        self.assertIn("idx_jobs_queue_rank", indexes)
        self.assertIn("idx_vacilos_user_timestamp", indexes)

    def test_write_context_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with database_manager.connection(self.db_path, write=True) as conn:
                conn.execute(
                    "INSERT INTO vacilos (user_id, user_name, timestamp) VALUES (1, 'Ana', 1)"
                )
                raise RuntimeError("rollback")

        with database_manager.connection(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM vacilos").fetchone()[0]
        self.assertEqual(total, 0)


class JobRepositoryTest(PersistenceTestCase):
    def setUp(self):
        super().setUp()
        self.jobs = JobRepository(self.db_path)

    def test_job_lifecycle_priority_and_invalid_transition(self):
        low = self.jobs.create_or_get(
            chat_id=1,
            user_id=10,
            url_norm="https://example.com/low",
            priority=1,
            idempotency_window=0,
            now=1,
        ).job
        high = self.jobs.create_or_get(
            chat_id=1,
            user_id=10,
            url_norm="https://example.com/high",
            priority=9,
            idempotency_window=0,
            now=2,
        ).job

        claimed = self.jobs.claim_next("worker-a", now=3)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.job_id, high.job_id)
        self.assertEqual(claimed.status, JobStatus.DOWNLOADING)
        self.assertEqual(claimed.attempt_count, 1)
        uploading = self.jobs.transition(claimed.job_id, JobStatus.UPLOADING, now=4)
        self.assertEqual(uploading.status, JobStatus.UPLOADING)
        completed = self.jobs.transition(uploading.job_id, JobStatus.COMPLETED, now=5)
        self.assertEqual(completed.completed_at, 5)

        with self.assertRaises(InvalidJobTransition):
            self.jobs.transition(low.job_id, JobStatus.UPLOADING, now=6)

    def test_idempotency_is_atomic_across_threads(self):
        def create(_index):
            repo = JobRepository(self.db_path)
            return repo.create_or_get(
                chat_id=77,
                user_id=9,
                url_norm="https://x.com/user/status/123",
                platform="twitter",
                idempotency_window=0,
                now=10,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(create, range(16)))

        self.assertEqual(len({claim.job.job_id for claim in claims}), 1)
        self.assertEqual(sum(not claim.reused for claim in claims), 1)

    def test_recent_completed_job_is_idempotent_only_inside_window(self):
        first = self.jobs.create_or_get(
            chat_id=5,
            user_id=1,
            url_norm="https://instagram.com/p/abc",
            idempotency_window=60,
            now=100,
        )
        self.jobs.claim_next("worker", now=101)
        self.jobs.transition(first.job.job_id, JobStatus.UPLOADING, now=102)
        self.jobs.transition(first.job.job_id, JobStatus.COMPLETED, now=103)

        recent = self.jobs.create_or_get(
            chat_id=5,
            user_id=2,
            url_norm="https://instagram.com/p/abc",
            idempotency_window=60,
            now=150,
        )
        expired = self.jobs.create_or_get(
            chat_id=5,
            user_id=2,
            url_norm="https://instagram.com/p/abc",
            idempotency_window=60,
            now=164,
        )

        self.assertTrue(recent.reused)
        self.assertEqual(recent.reason, "recently_completed")
        self.assertFalse(expired.reused)
        self.assertNotEqual(expired.job.job_id, first.job.job_id)

    def test_recover_interrupted_and_cleanup_terminal(self):
        interrupted = self.jobs.create_or_get(
            chat_id=1,
            user_id=1,
            url_norm="https://example.com/interrupted",
            idempotency_window=0,
            now=1,
        ).job
        self.jobs.claim_next("dead-worker", now=2)
        recovered = self.jobs.recover_interrupted(stale_after=10, now=20)
        self.assertEqual(recovered, [interrupted.job_id])
        restored = self.jobs.get(interrupted.job_id)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.status, JobStatus.QUEUED)
        self.assertIsNone(restored.worker_id)
        self.assertEqual(restored.error_type, "WorkerInterrupted")

        claimed = self.jobs.claim_next("new-worker", now=21)
        assert claimed is not None
        self.jobs.transition(claimed.job_id, JobStatus.FAILED, error=ValueError("bad"), now=22)
        self.assertEqual(self.jobs.cleanup_terminal(older_than=5, now=30), 1)
        self.assertIsNone(self.jobs.get(claimed.job_id))


class RateLimitRepositoryTest(PersistenceTestCase):
    def test_sliding_window_survives_repository_recreation(self):
        first = RateLimitRepository(self.db_path)
        self.assertTrue(
            first.consume("42", limit=2, window_seconds=10, now=0).allowed
        )
        self.assertTrue(
            first.consume("42", limit=2, window_seconds=10, now=1).allowed
        )

        recreated = RateLimitRepository(self.db_path)
        denied = recreated.consume("42", limit=2, window_seconds=10, now=5)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.used, 2)
        self.assertEqual(denied.retry_after, 5)
        self.assertTrue(
            recreated.consume("42", limit=2, window_seconds=10, now=10.1).allowed
        )

    def test_short_window_does_not_delete_another_subject_long_window(self):
        repo = RateLimitRepository(self.db_path)
        repo.consume("long", limit=1, window_seconds=3_600, now=1)
        repo.consume("short", limit=1, window_seconds=10, now=100)

        denied = repo.consume("long", limit=1, window_seconds=3_600, now=101)
        self.assertFalse(denied.allowed)


class MetricsAndLoggingTest(PersistenceTestCase):
    def test_metrics_cover_success_fallback_http_disk_queue_and_cookies(self):
        jobs = JobRepository(self.db_path)
        metrics = MetricsRepository(self.db_path)
        job = jobs.create_or_get(
            chat_id=1,
            user_id=2,
            url_norm="https://x.com/u/status/1",
            platform="twitter",
            now=1,
        ).job
        observer = PipelineObserver(metrics, jobs=jobs)

        observer.record_job("completed", platform="twitter", job_id=job.job_id)
        observer.record_fallback("yt_dlp", platform="twitter", job_id=job.job_id)
        observer.record_http(429, platform="twitter", job_id=job.job_id)
        observer.record_cookie_health("account-a", "healthy")
        health = observer.record_runtime_health(self.temp_dir.name)

        names = {entry["name"] for entry in metrics.summary()}
        self.assertIn("pipeline_jobs_total", names)
        self.assertIn("pipeline_fallback_total", names)
        self.assertIn("social_http_responses_total", names)
        self.assertIn("social_cookie_healthy", names)
        self.assertIn("pipeline_queue_depth", names)
        self.assertIn("disk_free_bytes", names)
        self.assertGreater(health["disk_total_bytes"], 0)
        self.assertEqual(
            metrics.latest_gauge(
                "social_cookie_healthy", platform="instagram", account="account-a"
            ),
            1,
        )

    def test_stage_records_duration_and_typed_failure(self):
        jobs = JobRepository(self.db_path)
        metrics = MetricsRepository(self.db_path)
        job = jobs.create_or_get(
            chat_id=1,
            user_id=2,
            url_norm="https://x.com/u/status/2",
            platform="twitter",
            now=1,
        ).job
        observer = PipelineObserver(metrics, jobs=jobs)

        with observer.job_scope(job.job_id, platform="twitter"):
            with observer.stage("extract"):
                pass
            with self.assertRaises(RateLimited):
                with observer.stage("download"):
                    raise RateLimited(
                        "limite",
                        platform="twitter",
                        stage="download",
                        status_code=429,
                    )

        timings = metrics.summary(name="pipeline_stage_duration_ms")
        errors = metrics.summary(name="pipeline_errors_total")
        self.assertEqual(sum(int(row["samples"]) for row in timings), 2)
        self.assertEqual(errors[0]["status"], "rate_limited")
        self.assertEqual(errors[0]["status_code"], 429)

    def test_json_log_contains_propagated_job_context(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredJsonFormatter())
        raw_logger = logging.getLogger("structured-test")
        raw_logger.handlers = [handler]
        raw_logger.propagate = False
        raw_logger.setLevel(logging.INFO)
        logger = get_logger("structured-test")

        with bind_log_context(job_id="job-123", platform="instagram"):
            logger.event(
                logging.INFO,
                "download_completed",
                "download pronto",
                stage="download",
                status="success",
            )

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["job_id"], "job-123")
        self.assertEqual(payload["platform"], "instagram")
        self.assertEqual(payload["stage"], "download")
        self.assertEqual(payload["event"], "download_completed")

    def test_sensitive_values_are_redacted(self):
        raw = (
            "token=abc123 cookie:sessionid=private "
            "Bearer secret.value https://example.com/media?id=42&token=nope"
        )
        redacted = redact_sensitive(raw)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("private", redacted)
        self.assertNotIn("secret.value", redacted)
        self.assertNotIn("id=42", redacted)


if __name__ == "__main__":
    unittest.main()
