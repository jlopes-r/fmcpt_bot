import subprocess
import sys
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
        self.assertIn("RETAIN_VENVS=2", script)
        self.assertIn('PYTHONPATH="$PREVIEW_DIR" ENV_FILE=/dev/null', script)

        powershell = (ROOT / "scripts" / "deploy.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[int]$RetainVenvs = 2", powershell)

    def test_on_vm_updater_keeps_concise_and_bounded_logs(self):
        updater = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
        self.assertIn('DEPLOY_LOG_KEEP="${DEPLOY_LOG_KEEP:-10}"', updater)
        self.assertIn('LOG_DIR="$REPO_DIR/data/logs/deploy"', updater)
        self.assertIn("tee -a \"$LOG_FILE\"", updater)
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', updater)
        self.assertIn("tail -n 80", updater)
        self.assertIn("prune_update_logs", updater)

    def test_operations_runbook_covers_update_rollback_and_recovery(self):
        runbook = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        for required in (
            "atualizar_bot",
            "data/logs/deploy",
            "Rollback automatico",
            "Recuperacao depois de uma falha tardia",
            ".deploy/last-successful",
            ".deploy/current-venv",
        ):
            self.assertIn(required, runbook)

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
        self.assertIn("TimeoutStartSec=30min", service)
        self.assertIn("KillMode=control-group", service)
        self.assertIn("CPUQuota=50%", service)
        self.assertIn("MemoryMax=512M", service)
        self.assertIn("IOSchedulingClass=idle", service)
        self.assertIn("check_social_contracts.py", service)
        self.assertIn("RandomizedDelaySec=30m", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("@REPO_DIR@", service)
        self.assertIn("--require-config", service)

    def test_contract_installer_renders_and_enables_units(self):
        installer = (ROOT / "scripts" / "install_social_contracts.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('s|@REPO_DIR@|$REPO_DIR|g', installer)
        self.assertIn("systemctl enable --now social-contracts.timer", installer)

    def test_contract_cli_can_run_directly_outside_repo(self):
        script = ROOT / "scripts" / "check_social_contracts.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--no-notify", completed.stdout)
        self.assertIn("--target", completed.stdout)
        self.assertIn("--timeout", completed.stdout)


if __name__ == "__main__":
    unittest.main()
