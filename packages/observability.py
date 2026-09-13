"""Contexto e formatacao de logs estruturados compartilhados pelos apps."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any


_LOG_CONTEXT: ContextVar[dict[str, object]] = ContextVar("log_context", default={})
_STRUCTURED_FIELDS = (
    "event",
    "job_id",
    "platform",
    "stage",
    "account",
    "status",
    "status_code",
    "error_code",
    "error_type",
    "retryable",
    "duration_ms",
    "fallback",
    "queue_depth",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|bot_token|api_hash|authorization|cookie|sessionid|csrftoken|password)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s]+", re.IGNORECASE)


def redact_sensitive(value: object) -> str:
    """Remove credenciais comuns e query strings antes de persistir/exibir."""

    text = str(value)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    return _URL_QUERY.sub(r"\1?[REDACTED]", text)


def get_log_context() -> dict[str, object]:
    return dict(_LOG_CONTEXT.get())


@contextmanager
def bind_log_context(**fields: object) -> Iterator[None]:
    """Propaga campos por chamadas async sem usar estado global mutavel."""

    merged = get_log_context()
    merged.update({key: value for key, value in fields.items() if value is not None})
    token: Token[dict[str, object]] = _LOG_CONTEXT.set(merged)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter que combina contexto do job e campos do evento."""

    def process(
        self,
        msg: object,
        kwargs: MutableMapping[str, Any],
    ) -> tuple[object, MutableMapping[str, Any]]:
        extra = get_log_context()
        if self.extra:
            extra.update(self.extra)
        call_extra = kwargs.get("extra")
        if isinstance(call_extra, Mapping):
            extra.update(call_extra)
        kwargs["extra"] = extra
        return msg, kwargs

    def event(self, level: int, event: str, message: str, **fields: object) -> None:
        self.log(level, message, extra={"event": event, **fields})


class StructuredJsonFormatter(logging.Formatter):
    """Uma linha JSON por evento, adequada para journald e agregadores."""

    @staticmethod
    def _json_default(value: object) -> str:
        return str(value)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        context = get_log_context()
        for key in _STRUCTURED_FIELDS:
            value = getattr(record, key, context.get(key))
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=self._json_default,
        )


def get_logger(name: str, **fields: object) -> ContextLoggerAdapter:
    return ContextLoggerAdapter(logging.getLogger(name), fields)


__all__ = [
    "ContextLoggerAdapter",
    "StructuredJsonFormatter",
    "bind_log_context",
    "get_log_context",
    "get_logger",
    "redact_sensitive",
]
