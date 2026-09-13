"""Verificacoes reais e opt-in dos contratos de extracao das redes sociais.

Os testes unitarios protegem o comportamento do nosso codigo. Este modulo faz
uma verificacao pequena contra URLs reais configuradas pelo operador e, por
isso, deve ser executado com baixa frequencia para nao pressionar as redes.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from apps.telegram_bot.instagram_extractor import (
    download_instagram,
    fetch_instagram_profile,
    get_profile_username,
)


SUPPORTED_DOMAINS = (
    "facebook.com",
    "fb.com",
    "fb.watch",
    "instagram.com",
    "pinterest.com",
    "pin.it",
    "threads.net",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
)


@dataclass(frozen=True)
class ContractTarget:
    name: str
    url: str


@dataclass(frozen=True)
class ContractResult:
    name: str
    ok: bool
    detail: str


def parse_contract_targets(raw: str) -> list[ContractTarget]:
    """Le ``SOCIAL_CONTRACT_URLS`` como objeto JSON ``nome -> URL``."""
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SOCIAL_CONTRACT_URLS nao e JSON valido: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SOCIAL_CONTRACT_URLS deve ser um objeto JSON")

    targets: list[ContractTarget] = []
    for raw_name, raw_url in payload.items():
        name = str(raw_name).strip()
        url = str(raw_url).strip()
        if not name or not url:
            raise ValueError("Cada contrato precisa ter nome e URL")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not any(
            host == domain or host.endswith(f".{domain}")
            for domain in SUPPORTED_DOMAINS
        ):
            raise ValueError(f"URL nao permitida no contrato {name!r}")
        targets.append(ContractTarget(name=name, url=url))
    return targets


def _is_instagram(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


def _is_instagram_profile(url: str) -> bool:
    if not _is_instagram(url):
        return False
    parts = [part for part in urlparse(url).path.split("/") if part]
    reserved = {"p", "reel", "reels", "stories", "s", "tv"}
    return len(parts) == 1 and parts[0].lower() not in reserved


def _generic_metadata(url: str) -> dict:
    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlistend": 2,
        "socket_timeout": 30,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


async def check_contract_target(
    target: ContractTarget,
    *,
    cookie_path: str = "",
    secondary_cookie_path: str = "",
) -> ContractResult:
    """Executa uma verificacao real e pequena para um unico alvo."""
    try:
        if _is_instagram_profile(target.url):
            profile = await fetch_instagram_profile(
                target.url,
                cookie_path,
                secondary_cookie_path=secondary_cookie_path,
            )
            expected = (get_profile_username(target.url) or "").lower()
            actual = str((profile or {}).get("username") or "").lower()
            if not profile or actual != expected:
                raise RuntimeError("perfil nao retornou dados coerentes")
            return ContractResult(target.name, True, f"perfil @{actual}")

        if _is_instagram(target.url):
            if not cookie_path and not secondary_cookie_path:
                raise RuntimeError("cookies do Instagram nao configurados")
            with tempfile.TemporaryDirectory(prefix="social-contract-") as folder:
                result = await download_instagram(
                    target.url,
                    cookie_path,
                    folder,
                    secondary_cookie_path=secondary_cookie_path,
                )
                media = list((result or {}).get("urls") or [])
                files = [Path(path) for path in (result or {}).get("files") or []]
                valid_files = [path for path in files if path.is_file() and path.stat().st_size]
                if not media and not valid_files:
                    raise RuntimeError("nenhuma midia foi extraida")
                count = len(media) + len(valid_files)
                return ContractResult(target.name, True, f"{count} midia(s)")

        info = await asyncio.to_thread(_generic_metadata, target.url)
        entries = info.get("entries") if isinstance(info, dict) else None
        if entries is not None and not any(entries):
            raise RuntimeError("extrator retornou uma lista vazia")
        if not info.get("id") and entries is None:
            raise RuntimeError("extrator nao retornou identificador")
        return ContractResult(target.name, True, "metadados extraidos")
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return ContractResult(target.name, False, detail[:240])


async def run_contract_checks(
    targets: list[ContractTarget],
    *,
    timeout: float = 180.0,
    cookie_path: str | None = None,
    secondary_cookie_path: str | None = None,
) -> list[ContractResult]:
    """Executa sequencialmente para reduzir bloqueios e rate limits."""
    primary = cookie_path if cookie_path is not None else os.getenv("IG_COOKIE_PATH", "")
    secondary = (
        secondary_cookie_path
        if secondary_cookie_path is not None
        else os.getenv("IG_SECONDARY_COOKIE_PATH", "")
    )
    results: list[ContractResult] = []
    for target in targets:
        try:
            result = await asyncio.wait_for(
                check_contract_target(
                    target,
                    cookie_path=primary,
                    secondary_cookie_path=secondary,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result = ContractResult(target.name, False, f"timeout apos {timeout:g}s")
        results.append(result)
    return results
