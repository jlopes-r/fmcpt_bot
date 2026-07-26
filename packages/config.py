import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", PROJECT_ROOT / "downloads"))
LOG_DIR = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))
SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", DATA_DIR / "sessions"))
TELEGRAM_BOT_DIR = PROJECT_ROOT / "apps" / "telegram_bot"
DEFAULT_ENV_FILE = TELEGRAM_BOT_DIR / ".env"


def load_environment(env_file: str | os.PathLike | None = None) -> Path:
    """Load .env from an explicit path, ENV_FILE, or the project default."""
    selected = Path(env_file or os.getenv("ENV_FILE") or DEFAULT_ENV_FILE)
    load_dotenv(selected)
    load_dotenv()
    return selected


def get_int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def parse_chat_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, DOWNLOADS_DIR, LOG_DIR, SESSIONS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def instagram_cookie_path() -> Path:
    return Path(os.getenv("IG_COOKIE_PATH", DATA_DIR / "instagram_cookies.txt"))


def mini_app_url() -> str:
    return os.getenv("MINI_APP_URL", "").strip()
