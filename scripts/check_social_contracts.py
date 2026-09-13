"""CLI para os testes reais de contrato das redes sociais."""

from __future__ import annotations

import argparse
import asyncio
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from apps.telegram_bot.social_contracts import parse_contract_targets, run_contract_checks


def _notify_admin(message: str) -> bool:
    """Avisa o administrador sem expor o token em logs ou argumentos."""
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        return False
    data = urllib.parse.urlencode({"chat_id": admin_id, "text": message}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=os.getenv("ENV_FILE", "apps/telegram_bot/.env"),
        help="arquivo .env usado pelo bot",
    )
    parser.add_argument(
        "--require-config",
        action="store_true",
        help="falha quando SOCIAL_CONTRACT_URLS nao estiver configurado",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    env_file = Path(args.env_file)
    load_dotenv(env_file if env_file.is_file() else None)
    try:
        targets = parse_contract_targets(os.getenv("SOCIAL_CONTRACT_URLS", ""))
    except ValueError as exc:
        print(f"ERRO configuracao: {exc}")
        return 2
    if not targets:
        print("Nenhum teste real configurado em SOCIAL_CONTRACT_URLS.")
        return 2 if args.require_config else 0

    try:
        timeout = max(10.0, float(os.getenv("SOCIAL_CONTRACT_TIMEOUT", "180")))
    except ValueError:
        print("ERRO configuracao: SOCIAL_CONTRACT_TIMEOUT precisa ser numerico")
        return 2

    results = await run_contract_checks(targets, timeout=timeout)
    for result in results:
        marker = "OK" if result.ok else "FALHA"
        print(f"[{marker}] {result.name}: {result.detail}")
    failures = sum(not result.ok for result in results)
    print(f"Contratos: {len(results) - failures}/{len(results)} passaram.")
    if failures:
        summary = "\n".join(
            f"- {result.name}: {result.detail}"
            for result in results
            if not result.ok
        )
        notified = await asyncio.to_thread(
            _notify_admin,
            "⚠️ Testes reais das redes sociais falharam:\n" + summary,
        )
        print("Administrador avisado pelo Telegram." if notified else "Aviso Telegram nao enviado.")
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
