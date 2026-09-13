import tempfile
import unittest
from pathlib import Path

from scripts import health_check


class HealthConfigTests(unittest.TestCase):
    def test_parses_quoted_env_without_inline_comment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                'API_ID=123\nAPI_HASH="hash value"\nBOT_TOKEN=12345:abcdefghijklmnopqrstuvwxyz # local\n',
                encoding="utf-8",
            )
            values = health_check.parse_env_file(path)
        self.assertEqual(values["API_HASH"], "hash value")
        self.assertEqual(values["BOT_TOKEN"], "12345:abcdefghijklmnopqrstuvwxyz")

    def test_rejects_missing_or_malformed_credentials(self):
        with self.assertRaises(health_check.HealthCheckError):
            health_check.validate_config({"API_ID": "1", "API_HASH": "x"})
        with self.assertRaises(health_check.HealthCheckError):
            health_check.validate_config(
                {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "not-a-token"}
            )


class StableServiceTests(unittest.TestCase):
    def test_requires_same_pid_for_stability_window(self):
        state = health_check.ServiceState("active", "running", 42, 0, "success", 0)
        clock = {"now": 0.0}

        def monotonic():
            return clock["now"]

        def sleep(seconds):
            clock["now"] += seconds

        result = health_check.wait_for_stable_service(
            "superbot.service",
            timeout=10,
            stability_seconds=3,
            state_reader=lambda _service: state,
            monotonic=monotonic,
            sleep=sleep,
        )
        self.assertEqual(result.main_pid, 42)

    def test_restart_during_window_fails(self):
        states = iter(
            [
                health_check.ServiceState("active", "running", 42, 0, "success", 0),
                health_check.ServiceState("active", "running", 42, 1, "success", 0),
            ]
        )
        clock = {"now": 0.0}

        def sleep(seconds):
            clock["now"] += seconds

        with self.assertRaisesRegex(health_check.HealthCheckError, "reiniciou"):
            health_check.wait_for_stable_service(
                "superbot.service",
                timeout=10,
                stability_seconds=3,
                state_reader=lambda _service: next(states),
                monotonic=lambda: clock["now"],
                sleep=sleep,
            )


if __name__ == "__main__":
    unittest.main()

