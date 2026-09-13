"""Executa a rotina de qualidade local do projeto, sem depender de CI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REQUIREMENTS = ROOT / "apps" / "telegram_bot" / "requirements.txt"
AUDIT_CACHE = ROOT / ".cache" / "pip-audit"


def _run(label: str, command: list[str]) -> bool:
    print(f"\n== {label} ==", flush=True)
    print(" ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        print(f"{label} falhou com codigo {result.returncode}.", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        action="store_true",
        help="inclui a auditoria online das dependencias declaradas",
    )
    args = parser.parse_args()

    python = sys.executable
    checks = [
        ("Ruff", [python, "-m", "ruff", "check", "apps", "packages", "scripts", "tests", "tools"]),
        ("Mypy", [python, "-m", "mypy"]),
        ("Testes", [python, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ]

    if args.audit:
        AUDIT_CACHE.mkdir(parents=True, exist_ok=True)
        print(
            "\nExcecao de auditoria documentada: PYSEC-2022-252 afetou a release "
            "maliciosa 1.8.5 de deep-translator; o projeto fixa 1.11.4.",
            flush=True,
        )
        checks.append(
            (
                "Auditoria de dependencias",
                [
                    python,
                    "-m",
                    "pip_audit",
                    "--requirement",
                    str(RUNTIME_REQUIREMENTS),
                    "--cache-dir",
                    str(AUDIT_CACHE),
                    "--progress-spinner",
                    "off",
                    "--ignore-vuln",
                    "PYSEC-2022-252",
                ],
            )
        )

    failures = [label for label, command in checks if not _run(label, command)]
    if failures:
        print(f"\nFalharam: {', '.join(failures)}", file=sys.stderr)
        return 1

    print("\nTodas as verificacoes locais passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
