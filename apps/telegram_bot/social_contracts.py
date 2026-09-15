"""Verificacoes reais e opt-in dos contratos de extracao das redes sociais.

Os testes unitarios protegem o comportamento do codigo. Este modulo exercita o
mesmo registro de extratores e a mesma preparacao de midia usados em producao,
contra URLs reais configuradas pelo operador. As verificacoes devem rodar com
baixa frequencia para nao pressionar as redes.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiohttp

from apps.telegram_bot.extractors.base import ExtractionContext
from apps.telegram_bot.extractors.registry import build_default_registry
from apps.telegram_bot.instagram_extractor import (
    fetch_instagram_profile,
    get_profile_username,
)
from apps.telegram_bot.models.media import MediaBundle, MediaKind
from apps.telegram_bot.services.download_manager import DownloadManager, detect_platform
from apps.telegram_bot.services.media_sender import MediaSender


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
SUPPORTED_KINDS = frozenset({"photo", "video", "audio", "document"})


@dataclass(frozen=True)
class BundleExpectation:
    min_items: int = 1
    kinds: tuple[MediaKind, ...] = ()
    require_text: bool = False
    allow_partial: bool = False


@dataclass(frozen=True)
class ContractTarget:
    name: str
    url: str
    platform: str = ""
    content_type: str = ""
    expectation: BundleExpectation = BundleExpectation()
    quote: BundleExpectation | None = None


@dataclass(frozen=True)
class ContractResult:
    name: str
    ok: bool
    detail: str


def _parse_non_negative_int(value: Any, *, field: str, target: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} de {target!r} deve ser inteiro nao negativo")
    return value


def _parse_kinds(value: Any, *, field: str, target: str) -> tuple[MediaKind, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} de {target!r} deve ser uma lista nao vazia")
    normalized = tuple(str(kind).strip().lower() for kind in value)
    invalid = sorted(set(normalized) - SUPPORTED_KINDS)
    if invalid:
        raise ValueError(f"{field} de {target!r} contem tipo invalido: {invalid[0]}")
    return cast(tuple[MediaKind, ...], normalized)


def _parse_expectation(
    payload: dict[str, Any],
    *,
    target: str,
    prefix: str = "",
) -> BundleExpectation:
    label = f"{prefix}." if prefix else ""
    min_items = _parse_non_negative_int(
        payload.get("min_items", 1),
        field=f"{label}min_items",
        target=target,
    )
    kinds = _parse_kinds(
        payload.get("kinds"),
        field=f"{label}kinds",
        target=target,
    )
    require_text = payload.get("require_text", False)
    allow_partial = payload.get("allow_partial", False)
    if not isinstance(require_text, bool):
        raise ValueError(f"{label}require_text de {target!r} deve ser booleano")
    if not isinstance(allow_partial, bool):
        raise ValueError(f"{label}allow_partial de {target!r} deve ser booleano")
    return BundleExpectation(
        min_items=min_items,
        kinds=kinds,
        require_text=require_text,
        allow_partial=allow_partial,
    )


def _validate_url(name: str, url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == domain or host.endswith(f".{domain}")
        for domain in SUPPORTED_DOMAINS
    ):
        raise ValueError(f"URL nao permitida no contrato {name!r}")


def parse_contract_targets(raw: str) -> list[ContractTarget]:
    """Le ``SOCIAL_CONTRACT_URLS`` como objeto JSON de contratos nomeados.

    O formato antigo ``{"nome": "https://..."}`` continua aceito. Para
    validar o resultado, o valor pode ser um objeto com ``url``, ``platform``,
    ``content_type``, ``min_items``, ``kinds``, ``require_text``,
    ``allow_partial`` e uma expectativa aninhada opcional em ``quote``.
    """
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SOCIAL_CONTRACT_URLS nao e JSON valido: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("SOCIAL_CONTRACT_URLS deve ser um objeto JSON")

    targets: list[ContractTarget] = []
    for raw_name, raw_config in payload.items():
        name = str(raw_name).strip()
        if isinstance(raw_config, str):
            config: dict[str, Any] = {"url": raw_config}
        elif isinstance(raw_config, dict):
            config = raw_config
        else:
            raise ValueError(f"Contrato {name!r} deve ser uma URL ou objeto")
        url = str(config.get("url") or "").strip()
        if not name or not url:
            raise ValueError("Cada contrato precisa ter nome e URL")
        _validate_url(name, url)

        platform = str(config.get("platform") or "").strip().lower()
        inferred_platform = detect_platform(url)
        if platform and platform != inferred_platform:
            raise ValueError(
                f"platform de {name!r} nao corresponde a URL: {inferred_platform}"
            )
        content_type = str(config.get("content_type") or "").strip().lower()
        expectation = _parse_expectation(config, target=name)
        raw_quote = config.get("quote")
        quote = None
        if raw_quote is not None:
            if not isinstance(raw_quote, dict):
                raise ValueError(f"quote de {name!r} deve ser um objeto")
            quote = _parse_expectation(raw_quote, target=name, prefix="quote")
        targets.append(
            ContractTarget(
                name=name,
                url=url,
                platform=platform or inferred_platform,
                content_type=content_type,
                expectation=expectation,
                quote=quote,
            )
        )
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


def _validate_bundle(
    bundle: MediaBundle,
    expectation: BundleExpectation,
    *,
    label: str,
) -> None:
    if len(bundle.items) < expectation.min_items:
        raise RuntimeError(
            f"{label} retornou {len(bundle.items)} midia(s); minimo "
            f"esperado: {expectation.min_items}"
        )
    if bundle.is_partial and not expectation.allow_partial:
        raise RuntimeError(
            f"{label} parcial: {len(bundle.items)}/{bundle.expected_items} midia(s)"
        )
    actual_kinds = {item.kind for item in bundle.items}
    missing_kinds = [kind for kind in expectation.kinds if kind not in actual_kinds]
    if missing_kinds:
        raise RuntimeError(
            f"{label} nao retornou tipo(s) esperado(s): {', '.join(missing_kinds)}"
        )
    if expectation.require_text and not (bundle.text or bundle.title).strip():
        raise RuntimeError(f"{label} nao retornou texto")


async def _prepare_bundle(
    bundle: MediaBundle,
    *,
    sender: MediaSender,
    directory: Path,
) -> MediaBundle:
    if not bundle.items:
        return bundle
    return await sender.prepare(bundle, directory)


async def check_contract_target(
    target: ContractTarget,
    *,
    cookie_path: str = "",
    secondary_cookie_path: str = "",
    max_media_bytes: int = 2_000_000_000,
    download_timeout: float = 180.0,
) -> ContractResult:
    """Executa extracao e preparacao reais para um unico alvo."""
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
            return ContractResult(target.name, True, f"instagram/profile @{actual}")

        with tempfile.TemporaryDirectory(prefix="social-contract-") as folder:
            directory = Path(folder)
            timeout = aiohttp.ClientTimeout(total=max(30.0, download_timeout))
            async with aiohttp.ClientSession(
                timeout=timeout,
                raise_for_status=False,
            ) as session:
                manager = DownloadManager(
                    directory,
                    max_filesize=max_media_bytes,
                    timeout=download_timeout,
                )
                registry = build_default_registry(
                    session,
                    manager,
                    instagram_cookie_path=cookie_path,
                    instagram_secondary_cookie_path=secondary_cookie_path,
                )
                bundle = await registry.extract(
                    target.url,
                    context=ExtractionContext(playlist_limit=50),
                )
                if target.platform and bundle.platform != target.platform:
                    raise RuntimeError(
                        f"plataforma retornada {bundle.platform!r}; "
                        f"esperada: {target.platform!r}"
                    )
                if target.content_type:
                    actual_type = str(bundle.metadata.get("content_type") or "").lower()
                    if actual_type != target.content_type:
                        raise RuntimeError(
                            f"tipo de conteudo {actual_type or 'ausente'}; "
                            f"esperado: {target.content_type}"
                        )

                sender = MediaSender(
                    None,
                    session=session,
                    max_media_bytes=max_media_bytes,
                )
                prepared = await _prepare_bundle(
                    bundle,
                    sender=sender,
                    directory=directory,
                )
                _validate_bundle(prepared, target.expectation, label="conteudo principal")

                quote_summary = ""
                if target.quote is not None:
                    raw_quote = bundle.metadata.get("quote")
                    if not isinstance(raw_quote, MediaBundle):
                        raise RuntimeError("tweet citado nao foi retornado")
                    prepared_quote = await _prepare_bundle(
                        raw_quote,
                        sender=sender,
                        directory=directory,
                    )
                    _validate_bundle(
                        prepared_quote,
                        target.quote,
                        label="tweet citado",
                    )
                    quote_summary = f"; citado={len(prepared_quote.items)}"

                kinds = ",".join(sorted({item.kind for item in prepared.items})) or "texto"
                strategy = str(bundle.metadata.get("extraction_strategy") or "")
                strategy_summary = f" via {strategy}" if strategy else ""
                content_type = str(bundle.metadata.get("content_type") or "")
                type_summary = f"/{content_type}" if content_type else ""
                return ContractResult(
                    target.name,
                    True,
                    f"{bundle.platform}{type_summary}: {len(prepared.items)} "
                    f"midia(s) [{kinds}]{strategy_summary}{quote_summary}",
                )
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return ContractResult(target.name, False, detail[:300])


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
    try:
        max_media_bytes = max(1, int(os.getenv("MAX_MEDIA_BYTES", "2000000000")))
    except ValueError:
        max_media_bytes = 2_000_000_000
    results: list[ContractResult] = []
    for target in targets:
        try:
            result = await asyncio.wait_for(
                check_contract_target(
                    target,
                    cookie_path=primary,
                    secondary_cookie_path=secondary,
                    max_media_bytes=max_media_bytes,
                    download_timeout=timeout,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result = ContractResult(target.name, False, f"timeout apos {timeout:g}s")
        results.append(result)
    return results
