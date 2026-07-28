import time
import logging
from collections.abc import Iterable


PRIVATE_ACCESS_CACHE_TTL = 10 * 60
ALLOWED_MEMBER_STATUSES = {"creator", "owner", "administrator", "member"}
_private_access_cache: dict[tuple[int, tuple[int, ...]], tuple[bool, float]] = {}
log = logging.getLogger("PrivateAccess")


def _status_name(status) -> str:
    value = getattr(status, "value", status)
    return str(value or "").split(".")[-1].lower()


def _member_allows_access(member) -> bool:
    status = _status_name(getattr(member, "status", None))
    if status in ALLOWED_MEMBER_STATUSES:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


def _chat_ids_key(chat_ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted(int(chat_id) for chat_id in chat_ids))


def clear_private_access_cache() -> None:
    _private_access_cache.clear()


async def user_is_authorized_group_member(client, user_id: int, chat_ids: Iterable[int]) -> bool:
    key = (int(user_id), _chat_ids_key(chat_ids))
    if not key[1]:
        return False

    cached = _private_access_cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    allowed = False
    for chat_id in key[1]:
        try:
            member = await client.get_chat_member(chat_id, user_id)
        except Exception:
            continue
        if _member_allows_access(member):
            allowed = True
            break

    _private_access_cache[key] = (allowed, now + PRIVATE_ACCESS_CACHE_TTL)
    return allowed


def is_private_chat(message) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", "")
    return _status_name(chat_type) == "private"


def is_group_chat(message) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", "")
    return _status_name(chat_type) in {"group", "supergroup"}


def _stop_message(message) -> None:
    stop = getattr(message, "stop_propagation", None)
    if callable(stop):
        stop()


async def guard_private_chat_access(client, message, chat_ids: Iterable[int], *, bot_label: str = "bot") -> bool:
    if not is_private_chat(message):
        return True

    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id and await user_is_authorized_group_member(client, int(user_id), chat_ids):
        return True

    username = getattr(user, "username", None) if user else None
    first_name = getattr(user, "first_name", None) if user else None
    log.warning(
        "unauthorized private chat blocked bot=%s user_id=%s username=%s first_name=%s authorized_groups=%s",
        bot_label,
        user_id,
        username,
        first_name,
        _chat_ids_key(chat_ids),
    )
    _stop_message(message)
    return False


async def guard_authorized_group_chat(client, message, chat_ids: Iterable[int], *, bot_label: str = "bot") -> bool:
    if not is_group_chat(message):
        return True

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    allowed_chat_ids = set(_chat_ids_key(chat_ids))
    if chat_id in allowed_chat_ids:
        return True

    log.warning(
        "unauthorized group chat blocked bot=%s chat_id=%s chat_title=%s chat_type=%s authorized_groups=%s",
        bot_label,
        chat_id,
        getattr(chat, "title", None),
        getattr(chat, "type", None),
        tuple(sorted(allowed_chat_ids)),
    )
    if chat_id is not None:
        try:
            await client.leave_chat(chat_id)
        except Exception:
            log.exception("failed to leave unauthorized group bot=%s chat_id=%s", bot_label, chat_id)
    _stop_message(message)
    return False
