"""Politicas centralizadas para downloads isolados via yt-dlp."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from apps.telegram_bot.downloaders import DownloadCancelled, baixar_com_ytdlp
from apps.telegram_bot.errors import (
    AuthenticationRequired,
    ContentUnavailable,
    DownloadTimeout,
    MediaTooLong,
    MediaTooLarge,
    RateLimited,
    StorageUnavailable,
)
from apps.telegram_bot.models.media import MediaBundle, MediaItem


DEFAULT_FORMAT = (
    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
)


@dataclass(frozen=True)
class PlatformDownloadPolicy:
    cookie_env: str = ""
    impersonate_env: str = ""
    default_impersonate: str = ""
    referer: str = ""
    fallback: str = "yt-dlp"


PLATFORM_DOWNLOAD_POLICIES = {
    "facebook": PlatformDownloadPolicy(
        "FACEBOOK_COOKIE_PATH", "FACEBOOK_IMPERSONATE", "chrome",
        "https://www.facebook.com/", "public-html",
    ),
    "instagram": PlatformDownloadPolicy(
        "IG_COOKIE_PATH", "INSTAGRAM_IMPERSONATE", "chrome",
        "https://www.instagram.com/", "account-pool",
    ),
    "tiktok": PlatformDownloadPolicy(
        "TIKTOK_COOKIE_PATH", "TIKTOK_IMPERSONATE", "chrome",
        "https://www.tiktok.com/", "yt-dlp-web",
    ),
    "threads": PlatformDownloadPolicy(
        "THREADS_COOKIE_PATH", "THREADS_IMPERSONATE", "chrome",
        "https://www.threads.net/", "yt-dlp-web",
    ),
    "pinterest": PlatformDownloadPolicy(
        "PINTEREST_COOKIE_PATH", "PINTEREST_IMPERSONATE", "chrome",
        "https://www.pinterest.com/", "yt-dlp-web",
    ),
    "twitter": PlatformDownloadPolicy(
        "X_COOKIE_PATH", fallback="vxtwitter-fxtwitter",
    ),
    "youtube": PlatformDownloadPolicy(
        "YOUTUBE_COOKIE_PATH", fallback="alternate-player-clients",
    ),
}
PLATFORM_COOKIE_ENV = {
    platform: policy.cookie_env
    for platform, policy in PLATFORM_DOWNLOAD_POLICIES.items()
    if policy.cookie_env
}


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in {"youtu.be", "youtube.com"} or host.endswith(".youtube.com"):
        return "youtube"
    for platform, domains in {
        "twitter": ("x.com", "twitter.com"),
        "instagram": ("instagram.com", "instagr.am"),
        "facebook": ("facebook.com", "fb.com", "fb.watch"),
        "tiktok": ("tiktok.com",),
        "threads": ("threads.net",),
        "pinterest": ("pinterest.com", "pin.it"),
    }.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return platform
    return "generic"


def _safe_impersonation(value: str) -> str:
    value = value.strip().lower()
    return value if re.fullmatch(r"[a-z0-9_-]{1,32}", value) else ""


def _csv_env(name: str, default: tuple[str, ...]) -> list[str]:
    values = [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]
    return values or list(default)


def classify_download_error(exc: BaseException, platform: str):
    message = str(exc).strip() or "o extrator nao retornou midia"
    lowered = message.lower()
    fields = {"platform": platform, "stage": "download"}
    if "video tem" in lowered and "limite" in lowered:
        return MediaTooLong(message, platform=platform, stage="duration")
    if any(marker in lowered for marker in ("429", "rate limit", "too many requests")):
        return RateLimited(message, status_code=429, **fields)
    if any(
        marker in lowered
        for marker in (
            "login required", "requires login", "you must log in",
            "private video", "only available for registered",
            "cookies are no longer valid",
        )
    ):
        return AuthenticationRequired(message, **fields)
    if any(marker in lowered for marker in ("larger than", "excede o limite", "too large")):
        return MediaTooLarge(message, **fields)
    if any(marker in lowered for marker in ("espaco livre", "espaço livre", "disco cheio")):
        return StorageUnavailable(message, **fields)
    return ContentUnavailable(message, **fields)


def _maximum_duration(info: dict) -> float:
    durations: list[float] = []

    def visit(item) -> None:
        if not isinstance(item, dict):
            return
        try:
            if item.get("duration") is not None:
                durations.append(float(item["duration"]))
        except (TypeError, ValueError):
            pass
        for child in item.get("entries") or []:
            visit(child)

    visit(info)
    return max(durations, default=0.0)


def build_download_options(
    platform: str,
    download_root: Path,
    *,
    max_filesize: int,
    allow_playlist: bool = False,
    playlist_limit: int = 20,
) -> dict:
    options: dict = {
        "format": DEFAULT_FORMAT,
        "paths": {"home": str(download_root)},
        "outtmpl": str(download_root / "%(id)s_%(autonumber)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not allow_playlist,
        "playlistend": max(1, min(playlist_limit, 50)),
        "merge_output_format": "mp4",
        "max_filesize": max_filesize,
    }
    policy = PLATFORM_DOWNLOAD_POLICIES.get(platform, PlatformDownloadPolicy())
    if platform == "twitter":
        options["extractor_args"] = {"twitter": {"api": ["syndication"]}}
    elif platform == "youtube":
        options["extractor_args"] = {
            "youtube": {
                "player_client": _csv_env(
                    "YOUTUBE_PLAYER_CLIENTS", ("android", "web")
                )
            }
        }
    if policy.referer:
        options["http_headers"] = {"Referer": policy.referer}
    if policy.impersonate_env:
        impersonate = _safe_impersonation(
            os.getenv(policy.impersonate_env, policy.default_impersonate)
        )
        if impersonate:
            options["impersonate"] = impersonate

    env_name = policy.cookie_env
    cookie_path = Path(os.getenv(env_name, "")) if env_name and os.getenv(env_name) else None
    if cookie_path and cookie_path.is_file():
        options["_source_cookiefile"] = str(cookie_path)
    return options


def _collect_file_items(info: dict) -> list[tuple[dict, Path]]:
    collected: list[tuple[dict, Path]] = []
    seen: set[str] = set()

    def visit(item: dict) -> None:
        if not isinstance(item, dict):
            return
        for child in item.get("entries") or []:
            visit(child)
        candidates = [item.get("filepath")]
        candidates.extend(
            download.get("filepath")
            for download in item.get("requested_downloads") or []
            if isinstance(download, dict)
        )
        for raw_path in candidates:
            if raw_path:
                path = Path(raw_path)
                key = str(path.resolve())
                if path.is_file() and key not in seen:
                    seen.add(key)
                    collected.append((item, path))

    visit(info)
    return collected


def _validate_twitter_identity(info: dict, requested_url: str) -> None:
    match = re.search(r"/(?:[^/]+)/status/(\d+)", requested_url)
    expected = match.group(1) if match else ""
    identity_text = " ".join(
        str(info.get(key) or "")
        for key in ("id", "display_id", "webpage_url", "original_url")
    )
    if not expected or expected not in identity_text:
        raise ContentUnavailable(
            "o fallback retornou midia que nao pertence ao tweet solicitado",
            platform="twitter",
            stage="identity",
        )


def _validate_facebook_identity(info: dict, requested_url: str) -> None:
    from apps.telegram_bot.facebook import parse_facebook_target

    target = parse_facebook_target(requested_url)
    expected = target.content_id if target else ""
    if not expected:
        return
    identity_values: list[str] = []

    def visit(item) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key, value in item.items():
                if key in {
                    "id", "display_id", "webpage_url", "original_url",
                    "url", "post_id", "video_id",
                } and isinstance(value, (str, int)):
                    identity_values.append(str(value))
                elif key == "entries":
                    visit(value)

    visit(info)
    if not any(expected == value or expected in value for value in identity_values):
        raise ContentUnavailable(
            "o fallback retornou midia de outro conteudo do Facebook",
            platform="facebook",
            stage="identity",
        )


class DownloadManager:
    def __init__(
        self,
        download_root: str | Path,
        *,
        max_filesize: int = 2_000_000_000,
        timeout: float = 7200,
    ) -> None:
        self.download_root = Path(download_root)
        self.max_filesize = max_filesize
        self.timeout = timeout

    async def download(
        self,
        url: str,
        *,
        platform: str | None = None,
        allow_playlist: bool = False,
        playlist_limit: int = 20,
        duration_limit: float | None = None,
        status=None,
        cancel_event=None,
        reply_markup=None,
    ) -> MediaBundle:
        platform = platform or detect_platform(url)
        options = build_download_options(
            platform,
            self.download_root,
            max_filesize=self.max_filesize,
            allow_playlist=allow_playlist,
            playlist_limit=playlist_limit,
        )
        if duration_limit is not None and duration_limit > 0:
            options["_duration_limit"] = float(duration_limit)
        source_cookie = options.pop("_source_cookiefile", "")
        cookie_copy = ""
        if source_cookie:
            descriptor, cookie_copy = tempfile.mkstemp(prefix=f"{platform}-cookies-", suffix=".txt")
            os.close(descriptor)
            shutil.copyfile(source_cookie, cookie_copy)
            options["cookiefile"] = cookie_copy
        try:
            try:
                info = await baixar_com_ytdlp(
                    url,
                    options,
                    timeout=self.timeout,
                    msg_espera=status,
                    cancel_event=cancel_event,
                    reply_markup=reply_markup,
                )
            except asyncio.TimeoutError as exc:
                raise DownloadTimeout(
                    "download excedeu o prazo",
                    platform=platform,
                    stage="download",
                ) from exc
            except DownloadCancelled:
                raise
            except (yt_dlp.utils.DownloadError, RuntimeError, OSError) as exc:
                raise classify_download_error(exc, platform) from exc

            if platform == "twitter":
                _validate_twitter_identity(info, url)
            elif platform == "facebook":
                _validate_facebook_identity(info, url)
            files = _collect_file_items(info)
            if not files:
                duration = _maximum_duration(info)
                if duration_limit is not None and duration > duration_limit:
                    raise MediaTooLong(
                        f"midia com {duration:g}s excede o limite de {duration_limit:g}s",
                        platform=platform,
                        stage="duration",
                    )
                raise ContentUnavailable(
                    "yt-dlp nao produziu arquivos",
                    platform=platform,
                    stage="download",
                )
            items: list[MediaItem] = []
            for index, (metadata, path) in enumerate(files):
                if path.stat().st_size > self.max_filesize:
                    raise MediaTooLarge(
                        "arquivo final excede o limite",
                        platform=platform,
                        stage="download",
                    )
                suffix = path.suffix.lower()
                kind = "video" if suffix in {".mp4", ".mov", ".m4v", ".webm", ".mkv"} else "photo"
                items.append(
                    MediaItem(
                        str(path),
                        kind,
                        index=index,
                        duration=metadata.get("duration"),
                        width=metadata.get("width"),
                        height=metadata.get("height"),
                        source_id=str(metadata.get("id") or ""),
                    )
                )
            return MediaBundle(
                platform=platform,
                items=tuple(items),
                title=str(info.get("title") or info.get("description") or ""),
                author=str(info.get("uploader") or info.get("channel") or ""),
                source_url=url,
                source_id=str(info.get("id") or ""),
                expected_items=len(files),
                metadata={
                    "extractor": info.get("extractor_key") or info.get("extractor"),
                    "download_fallback": PLATFORM_DOWNLOAD_POLICIES.get(
                        platform, PlatformDownloadPolicy()
                    ).fallback,
                },
            )
        finally:
            if cookie_copy:
                Path(cookie_copy).unlink(missing_ok=True)
