"""Safe persistence helpers for JSON state shared by multiple services."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

try:  # Linux production hosts.
    import fcntl
except ImportError:  # pragma: no cover - Windows development hosts.
    fcntl = None

try:  # Windows development hosts.
    import msvcrt
except ImportError:  # pragma: no cover - Linux production hosts.
    msvcrt = None


JsonValue = dict[str, Any] | list[Any]
T = TypeVar("T", dict[str, Any], list[Any])


def _default_copy(default: T) -> T:
    return copy.deepcopy(default)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Serialize writers across processes while preserving Windows support."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.seek(0)
            lock_file.write(b"0")
            lock_file.flush()

        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows only.
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows only.
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _load_unlocked(path: Path, default: T) -> T:
    if not path.exists():
        return _default_copy(default)
    try:
        with open(path, "r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return _default_copy(default)
    return value if isinstance(value, type(default)) else _default_copy(default)


def load_json(path: Path, default: T) -> T:
    """Read a JSON document, returning an independent default on invalid data."""
    return _load_unlocked(path, default)


def _save_unlocked(path: Path, data: JsonValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_json(path: Path, data: JsonValue) -> None:
    """Atomically replace a JSON document without exposing partial writes."""
    with _exclusive_lock(path):
        _save_unlocked(path, data)


def update_json(path: Path, default: T, updater: Callable[[T], T | None]) -> T:
    """Read, update, and persist one JSON document under one process lock."""
    with _exclusive_lock(path):
        current = _load_unlocked(path, default)
        updated = updater(current)
        result = current if updated is None else updated
        if not isinstance(result, type(default)):
            raise TypeError(f"JSON update for {path} returned {type(result).__name__}")
        _save_unlocked(path, result)
        return result


def merge_mapping_changes(
    current: dict[str, Any],
    snapshot: dict[str, Any],
    local: dict[str, Any],
) -> dict[str, Any]:
    """Apply local mapping changes while preserving unrelated concurrent writes."""
    result = copy.deepcopy(current)
    missing = object()

    def key_ignoring_case(items: dict[str, Any], name: str) -> str | None:
        return next((item for item in items if item.lower() == name.lower()), None)

    for name in snapshot:
        if name not in local:
            key = key_ignoring_case(result, name)
            if key:
                del result[key]

    for name, value in local.items():
        previous = snapshot.get(name, missing)
        if previous is missing or previous != value:
            key = key_ignoring_case(result, name)
            if key and key != name:
                del result[key]
            result[name] = value

    return result
