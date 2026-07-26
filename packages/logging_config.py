import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_rotating_logging(log_dir: Path, filename: str = "bot.log") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_handler = RotatingFileHandler(
        log_dir / filename,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[log_handler, logging.StreamHandler()],
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.ERROR)
