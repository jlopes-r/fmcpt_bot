"""Politicas reutilizaveis dos handlers de comandos."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable


def build_admin_only(
    admin_id: Callable[[], int],
    *,
    logger: logging.Logger | None = None,
):
    """Cria um decorator que resolve o administrador no momento da chamada."""
    command_log = logger or logging.getLogger(__name__)

    def decorator(function):
        @wraps(function)
        async def wrapped(client, message, *args, **kwargs):
            configured_admin = int(admin_id() or 0)
            user_id = getattr(getattr(message, "from_user", None), "id", None)
            if not configured_admin or user_id != configured_admin:
                command_log.warning(
                    "admin_command_denied user_id=%s admin_configured=%s",
                    user_id,
                    bool(configured_admin),
                )
                try:
                    await message.reply_text(
                        "⛔ Acesso negado. Este comando é exclusivo do administrador."
                    )
                except Exception:
                    command_log.warning(
                        "admin_denial_reply_failed user_id=%s", user_id, exc_info=True
                    )
                return None
            return await function(client, message, *args, **kwargs)

        return wrapped

    return decorator

