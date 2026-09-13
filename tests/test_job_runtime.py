import asyncio
import tempfile
import unittest
from pathlib import Path

from apps.telegram_bot.services.job_runtime import DurableJobRuntime
from packages.database import database_manager
from packages.database.repositories import (
    JobRepository,
    JobStatus,
    RateLimitRepository,
)


class DurableJobRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "runtime.db")
        database_manager.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def runtime(self, worker_id="worker-a"):
        return DurableJobRuntime(
            JobRepository(self.db_path),
            RateLimitRepository(self.db_path),
            worker_id=worker_id,
            heartbeat_interval=1,
        )

    async def test_concurrent_submit_claim_upload_and_complete(self):
        runtime = self.runtime()

        first, second = await asyncio.gather(
            runtime.submit(
                chat_id=10,
                user_id=20,
                url_norm="https://x.com/user/status/1",
                platform="twitter",
                metadata={"message_id": 30},
            ),
            runtime.submit(
                chat_id=10,
                user_id=20,
                url_norm="https://x.com/user/status/1",
                platform="twitter",
                metadata={"message_id": 30},
            ),
        )

        self.assertEqual(first.job.job_id, second.job.job_id)
        self.assertEqual(sum(claim.reused for claim in (first, second)), 1)
        created = first if not first.reused else second
        downloading = await runtime.claim(created.job.job_id)
        self.assertEqual(downloading.status, JobStatus.DOWNLOADING)
        uploading = await runtime.mark_uploading(created.job.job_id)
        self.assertEqual(uploading.status, JobStatus.UPLOADING)
        completed = await runtime.complete(
            created.job.job_id,
            metadata={"item_count": 2},
        )
        self.assertEqual(completed.status, JobStatus.COMPLETED)
        self.assertEqual(completed.metadata["item_count"], 2)

    async def test_text_only_job_can_complete_without_upload_transition(self):
        runtime = self.runtime()
        claim = await runtime.submit(
            chat_id=1,
            user_id=2,
            url_norm="https://x.com/user/status/2",
            platform="twitter",
        )
        await runtime.claim(claim.job.job_id)

        completed = await runtime.complete(
            claim.job.job_id,
            metadata={"text_only": True},
        )

        self.assertEqual(completed.status, JobStatus.COMPLETED)

    async def test_startup_requeues_interrupted_and_keeps_queued_jobs(self):
        old_runtime = self.runtime("old-worker")
        interrupted = await old_runtime.submit(
            chat_id=1,
            user_id=2,
            url_norm="https://instagram.com/p/interrupted",
            platform="instagram",
            metadata={"message_id": 50, "url": "https://instagram.com/p/interrupted"},
        )
        await old_runtime.claim(interrupted.job.job_id)
        queued = await old_runtime.submit(
            chat_id=1,
            user_id=2,
            url_norm="https://instagram.com/p/queued",
            platform="instagram",
            metadata={"message_id": 51, "url": "https://instagram.com/p/queued"},
        )

        report = await self.runtime("new-worker").recover_startup()

        self.assertEqual(report.recovered_ids, (interrupted.job.job_id,))
        self.assertEqual(
            {job.job_id for job in report.pending},
            {interrupted.job.job_id, queued.job.job_id},
        )
        self.assertTrue(all(job.status is JobStatus.QUEUED for job in report.pending))

    async def test_rate_limit_survives_runtime_recreation(self):
        runtime = self.runtime()
        self.assertTrue(
            (await runtime.allow("42", limit=2, window_seconds=60)).allowed
        )
        self.assertTrue(
            (await runtime.allow("42", limit=2, window_seconds=60)).allowed
        )

        recreated = self.runtime("worker-b")
        denied = await recreated.allow("42", limit=2, window_seconds=60)

        self.assertFalse(denied.allowed)
        self.assertGreater(denied.retry_after, 0)


if __name__ == "__main__":
    unittest.main()
