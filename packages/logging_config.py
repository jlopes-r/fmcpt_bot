import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from packages.observability import StructuredJsonFormatter


def configure_rotating_logging(
    log_dir: Path,
    filename: str = "bot.log",
    *,
    structured: bool | None = None,
) -> None:
    """Configura arquivo rotativo e console.

    JSON e o padrao para que campos como ``job_id`` e ``platform`` possam ser
    consultados. ``LOG_FORMAT=text`` preserva a exibicao antiga localmente.
    """

    log_dir.mkdir(parents=True, exist_ok=True)
    log_handler = RotatingFileHandler(
        log_dir / filename,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    console_handler = logging.StreamHandler()
    use_structured = (
        os.getenv("LOG_FORMAT", "json").strip().lower() != "text"
        if structured is None
        else structured
    )
    if use_structured:
        formatter: logging.Formatter = StructuredJsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[log_handler, console_handler],
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.ERROR)
