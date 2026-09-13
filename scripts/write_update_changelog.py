#!/usr/bin/env python3
"""Create the ignored update notification consumed by ``super_bot.py``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


BRAZIL_TIME = timezone(timedelta(hours=-3))


def collect_commits(repo: Path, old_revision: str, new_revision: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--no-merges",
            "--format=%h%x09%s",
            f"{old_revision}..{new_revision}",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    commits: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        revision, separator, message = line.partition("\t")
        if separator and revision:
            commits.append({"hash": revision, "message": message or "(sem mensagem)"})
    return commits


def write_changelog(
    output: Path,
    commits: list[dict[str, str]],
    *,
    now: datetime | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(BRAZIL_TIME)).astimezone(BRAZIL_TIME)
    payload = {
        "commits": commits,
        "updated_at": timestamp.strftime("%d/%m/%Y as %H:%M"),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commits = collect_commits(args.repo, args.old, args.new)
    if commits:
        write_changelog(args.output, commits)
        print(f"Changelog preparado com {len(commits)} commit(s).")
    else:
        print("Nenhum commit novo para notificar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
