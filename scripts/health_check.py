#!/usr/bin/env python3
"""Post-deploy health check for the Super Bot service.

The script deliberately uses only Python's standard library so it can run with
the VM's system Python even when a release virtualenv is broken.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HealthCheckError(RuntimeError):
    """A deploy health assertion failed."""


@dataclass(frozen=True)
class ServiceState:
    active_state: str
    sub_state: str
    main_pid: int
    n_restarts: int
    result: str
    exec_main_status: int


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the simple KEY=VALUE subset used by the bot's .env file."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise HealthCheckError(f"arquivo de ambiente indisponivel: {path}") from exc

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not ENV_KEY_RE.fullmatch(key):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def validate_config(env: Mapping[str, str], token_env: str = "BOT_TOKEN") -> str:
    missing = [name for name in ("API_ID", "API_HASH", token_env) if not env.get(name)]
    if missing:
        raise HealthCheckError(
            "configuracao obrigatoria ausente: " + ", ".join(sorted(missing))
        )
    try:
        if int(env["API_ID"]) <= 0:
            raise ValueError
    except ValueError as exc:
        raise HealthCheckError("API_ID deve ser um inteiro positivo") from exc

    token = env[token_env].strip()
    if not TOKEN_RE.fullmatch(token):
        raise HealthCheckError(f"{token_env} possui formato invalido")
    return token


def _run_systemctl(service: str) -> ServiceState:
    command = [
        "systemctl",
        "show",
        service,
        "--no-pager",
        "--property=ActiveState",
        "--property=SubState",
        "--property=MainPID",
        "--property=NRestarts",
        "--property=Result",
        "--property=ExecMainStatus",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise HealthCheckError("nao foi possivel consultar o systemd")

    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value.strip()
    try:
        return ServiceState(
            active_state=properties.get("ActiveState", "unknown"),
            sub_state=properties.get("SubState", "unknown"),
            main_pid=int(properties.get("MainPID", "0")),
            n_restarts=int(properties.get("NRestarts", "0")),
            result=properties.get("Result", "unknown"),
            exec_main_status=int(properties.get("ExecMainStatus", "0")),
        )
    except ValueError as exc:
        raise HealthCheckError("resposta inesperada do systemd") from exc


def wait_for_stable_service(
    service: str,
    timeout: float,
    stability_seconds: float,
    *,
    state_reader: Callable[[str], ServiceState] = _run_systemctl,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ServiceState:
    deadline = monotonic() + timeout
    stable_since: float | None = None
    stable_pid = 0
    restart_count = -1
    latest: ServiceState | None = None

    while monotonic() < deadline:
        latest = state_reader(service)
        running = (
            latest.active_state == "active"
            and latest.sub_state == "running"
            and latest.main_pid > 0
        )
        if running:
            if latest.main_pid != stable_pid:
                stable_pid = latest.main_pid
                restart_count = latest.n_restarts
                stable_since = monotonic()
            elif latest.n_restarts != restart_count:
                raise HealthCheckError("o servico reiniciou durante o health check")
            elif stable_since is not None and monotonic() - stable_since >= stability_seconds:
                return latest
        else:
            stable_since = None
            stable_pid = 0
            restart_count = -1
        sleep(min(1.0, max(0.0, deadline - monotonic())))

    detail = "estado desconhecido"
    if latest is not None:
        detail = (
            f"{latest.active_state}/{latest.sub_state}, result={latest.result}, "
            f"status={latest.exec_main_status}"
        )
    raise HealthCheckError(f"servico nao estabilizou em {timeout:.0f}s ({detail})")


def read_process_command(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError as exc:
        raise HealthCheckError("processo principal desapareceu durante a verificacao") from exc
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


def validate_process(pid: int, expected_python: Path | None, entrypoint: str) -> None:
    command = read_process_command(pid)
    if not any(part.endswith(entrypoint) for part in command):
        raise HealthCheckError(f"o processo ativo nao esta executando {entrypoint}")
    if expected_python is not None:
        expected = str(expected_python)
        resolved = str(expected_python.resolve())
        executable = command[0] if command else ""
        executable_resolved = str(Path(executable).resolve()) if executable else ""
        if executable not in {expected, resolved} and executable_resolved != resolved:
            raise HealthCheckError("o servico nao iniciou com o venv da nova release")


def check_runtime_storage(repo: Path, minimum_free_mb: int) -> None:
    data_dir = repo / "data"
    downloads_dir = repo / "downloads"
    for directory in (data_dir, downloads_dir):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(prefix=".health-", dir=directory, delete=True) as handle:
                handle.write(b"ok")
                handle.flush()
        except OSError as exc:
            raise HealthCheckError(f"diretorio sem escrita: {directory}") from exc

    usage = shutil.disk_usage(repo)
    free_mb = usage.free // (1024 * 1024)
    if free_mb < minimum_free_mb:
        raise HealthCheckError(
            f"espaco livre insuficiente: {free_mb} MiB (minimo {minimum_free_mb} MiB)"
        )

    database = data_dir / "bocadeleite.db"
    if not database.is_file():
        raise HealthCheckError("banco SQLite nao foi inicializado pelo bot")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        try:
            result = connection.execute("PRAGMA quick_check(1)").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise HealthCheckError("nao foi possivel ler o banco SQLite") from exc
    if not result or result[0] != "ok":
        raise HealthCheckError("verificacao de integridade do SQLite falhou")


def telegram_get_me(token: str, timeout: float = 10.0) -> dict[str, object]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getMe",
        headers={"User-Agent": "superbot-health-check/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HealthCheckError(f"Telegram getMe retornou HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise HealthCheckError("Telegram getMe ficou indisponivel") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise HealthCheckError("Telegram getMe rejeitou as credenciais")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("is_bot") is not True:
        raise HealthCheckError("Telegram getMe retornou uma conta inesperada")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verifica a saude do Super Bot")
    parser.add_argument("--repo", type=Path, default=Path("/home/juanl/bot"))
    parser.add_argument("--service", default="superbot.service")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--expected-python", type=Path)
    parser.add_argument("--entrypoint", default="super_bot.py")
    parser.add_argument("--token-env", default="BOT_TOKEN")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--stability-seconds", type=float, default=12.0)
    parser.add_argument("--minimum-free-mb", type=int, default=256)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Valida apenas configuracao e armazenamento antes da troca.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    env_file = args.env_file or repo / "apps" / "telegram_bot" / ".env"
    try:
        file_env = parse_env_file(env_file)
        merged_env = {**file_env, **os.environ}
        token = validate_config(merged_env, args.token_env)
        if args.config_only:
            # The database can legitimately be absent on a first installation.
            for directory in (repo / "data", repo / "downloads"):
                directory.mkdir(parents=True, exist_ok=True)
            check_path = repo / "data"
            if not os.access(check_path, os.W_OK):
                raise HealthCheckError(f"diretorio sem escrita: {check_path}")
            print("OK - configuracao de deploy valida")
            return 0

        state = wait_for_stable_service(
            args.service,
            args.timeout,
            args.stability_seconds,
        )
        validate_process(state.main_pid, args.expected_python, args.entrypoint)
        check_runtime_storage(repo, args.minimum_free_mb)
        bot = telegram_get_me(token, timeout=min(10.0, args.timeout))
        username = bot.get("username") or bot.get("id") or "desconhecido"
        print(
            f"OK - {args.service} estavel (pid={state.main_pid}); "
            f"SQLite e Telegram (@{username}) respondendo"
        )
        return 0
    except HealthCheckError as exc:
        print(f"FALHA - {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
