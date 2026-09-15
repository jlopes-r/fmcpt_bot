import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.logging_config import configure_rotating_logging


class LoggingConfigTests(unittest.TestCase):
    def test_existing_root_handler_does_not_open_abandoned_log_file(self):
        root = logging.getLogger()
        existing = logging.NullHandler()
        original_handlers = list(root.handlers)
        root.handlers = [existing]
        try:
            with tempfile.TemporaryDirectory() as folder:
                with patch(
                    "packages.logging_config.RotatingFileHandler"
                ) as rotating_handler:
                    configure_rotating_logging(Path(folder), "bot.log")
                rotating_handler.assert_not_called()
        finally:
            root.handlers = original_handlers


if __name__ == "__main__":
    unittest.main()
