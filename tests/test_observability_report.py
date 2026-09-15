import tempfile
import unittest
from pathlib import Path

from packages.database import database_manager
from packages.database.repositories import JobRepository, MetricsRepository
from scripts.observability_report import build_report, format_report


class ObservabilityReportTests(unittest.TestCase):
    def test_report_combines_queue_and_recent_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "report.db"
            database_manager.init_db(db_path)
            JobRepository(db_path).create_or_get(
                chat_id=1,
                user_id=2,
                url_norm="https://x.com/u/status/1",
                platform="twitter",
                now=90,
            )
            MetricsRepository(db_path).record(
                "pipeline_jobs_total",
                platform="twitter",
                status="queued",
                timestamp=95,
            )

            report = build_report(db_path, hours=1, now=100)
            rendered = format_report(report)

        self.assertEqual(report["queue"]["queued"], 1)
        self.assertEqual(len(report["metrics"]), 1)
        self.assertIn("pipeline_jobs_total", rendered)
        self.assertIn("queued=1", rendered)


if __name__ == "__main__":
    unittest.main()
