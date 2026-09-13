import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    def test_deploy_has_preflight_health_and_automatic_rollback(self):
        script = (ROOT / "scripts" / "deploy_remote.sh").read_text(encoding="utf-8")
        self.assertIn("Running the candidate unit tests before any restart", script)
        self.assertIn("Health check failed; rolling back", script)
        self.assertIn("current-venv", script)
        self.assertIn("pip check", script)

    def test_runtime_updater_is_not_reintroduced(self):
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (ROOT / "apps" / "telegram_bot").rglob("*.py")
        ).lower()
        self.assertNotIn("update_ytdlp", source)
        self.assertNotIn("pip install -u yt-dlp", source)

    def test_contract_timer_is_bounded_and_persistent(self):
        service = (ROOT / "scripts" / "social-contracts.service").read_text(
            encoding="utf-8"
        )
        timer = (ROOT / "scripts" / "social-contracts.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("Type=oneshot", service)
        self.assertIn("check_social_contracts.py", service)
        self.assertIn("RandomizedDelaySec=30m", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()

