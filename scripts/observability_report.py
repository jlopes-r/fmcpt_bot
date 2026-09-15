"""Resumo local dos jobs e metricas persistidas pelo pipeline social."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.database import database_manager  # noqa: E402
from packages.database.repositories import (  # noqa: E402
    JobRepository,
    MetricsRepository,
)


def build_report(
    db_path: str | Path,
    *,
    hours: float = 24.0,
    now: float | None = None,
) -> dict[str, object]:
    generated_at = float(time.time() if now is None else now)
    window_seconds = max(0.0, float(hours)) * 3_600
    return {
        "generated_at": generated_at,
        "window_hours": max(0.0, float(hours)),
        "queue": JobRepository(db_path).queue_depth(),
        "metrics": MetricsRepository(db_path).summary(
            since=generated_at - window_seconds
        ),
    }


def format_report(report: dict[str, object]) -> str:
    queue = report.get("queue") or {}
    metrics = report.get("metrics") or []
    lines = [
        f"Janela: ultimas {report['window_hours']:g} hora(s)",
        "Fila: " + ", ".join(
            f"{status}={depth}"
            for status, depth in sorted(dict(queue).items())
        ),
        "Metricas:",
    ]
    if not metrics:
        lines.append("  nenhum evento registrado nessa janela")
        return "\n".join(lines)
    for row in metrics:
        fields = [str(row["name"])]
        for key in ("platform", "stage", "status"):
            if row.get(key):
                fields.append(f"{key}={row[key]}")
        fields.extend(
            (
                f"amostras={row['samples']}",
                f"total={float(row['total']):.3f}",
                f"media={float(row['average']):.3f}",
            )
        )
        lines.append("  " + " ".join(fields))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mostra jobs e metricas persistidas do bot.",
    )
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(database_manager.DB_PATH),
    )
    args = parser.parse_args()
    report = build_report(args.database, hours=args.hours)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
