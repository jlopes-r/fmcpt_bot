import json
from html import escape

from packages.command_catalog import CommandSpec, autocomplete_commands, grouped_commands

BOT_API_BASE = "https://api.telegram.org/bot"


def build_help_text(title: str, commands: tuple[CommandSpec, ...]) -> str:
    parts = [f"**{title}**", ""]
    for category, category_commands in grouped_commands(commands).items():
        parts.append(f"**{category}**")
        for command in category_commands:
            usage = command.usage or f"/{command.name}"
            aliases = f" (aliases: {', '.join('/' + a for a in command.aliases)})" if command.aliases else ""
            parts.append(f"- `{usage}` - {command.description}{aliases}")
        parts.append("")
    return "\n".join(parts).strip()


def build_command_menu_text(title: str, commands: tuple[CommandSpec, ...], has_mini_app: bool) -> str:
    suffix = "\n\nUse o botao abaixo para abrir o painel completo." if has_mini_app else ""
    return build_help_text(title, commands) + suffix


def build_help_html(title: str, commands: tuple[CommandSpec, ...]) -> str:
    parts = [f"<b>{escape(title)}</b>", ""]
    for category, category_commands in grouped_commands(commands).items():
        parts.append(f"<b>{escape(category)}</b>")
        for command in category_commands:
            usage = command.usage or f"/{command.name}"
            aliases = f" (aliases: {', '.join('/' + a for a in command.aliases)})" if command.aliases else ""
            parts.append(
                f"- <code>{escape(usage)}</code> - {escape(command.description + aliases)}"
            )
        parts.append("")
    return "\n".join(parts).strip()


def build_command_menu_html(title: str, commands: tuple[CommandSpec, ...], has_mini_app: bool) -> str:
    suffix = "\n\nUse o botao no privado do bot para abrir o painel completo." if has_mini_app else ""
    return build_help_html(title, commands) + suffix


def build_bot_commands(commands: tuple[CommandSpec, ...]):
    from pyrogram.types import BotCommand

    return [
        BotCommand(command.name, command.description[:60])
        for command in autocomplete_commands(commands)
    ]


def build_bot_commands_payload(commands: tuple[CommandSpec, ...]) -> list[dict]:
    payload = []
    for command in autocomplete_commands(commands):
        item = {
            "command": command.name,
            "description": command.description[:60],
        }
        if command.ephemeral:
            item["is_ephemeral"] = True
        payload.append(item)
    return payload


def build_mini_app_markup(url: str, label: str = "Abrir painel"):
    if not url:
        return None
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, web_app=WebAppInfo(url=url))]
    ])


def build_mini_app_markup_payload(url: str, label: str = "Abrir painel") -> dict | None:
    if not url:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "web_app": {"url": url}}],
        ]
    }


def _is_button_type_invalid(exc: Exception) -> bool:
    return exc.__class__.__name__ == "ButtonTypeInvalid" or "BUTTON_TYPE_INVALID" in str(exc)


def _is_group_chat(message) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", "")
    chat_type_name = getattr(chat_type, "name", "")
    normalized = str(chat_type_name or chat_type).lower()
    return normalized in {
        "chat.type.group",
        "chat.type.supergroup",
        "chattype.group",
        "chattype.supergroup",
        "group",
        "supergroup",
    }


async def _bot_api_post(bot_token: str, method: str, payload: dict) -> dict:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{BOT_API_BASE}{bot_token}/{method}", json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400 or not data.get("ok"):
                description = data.get("description") or response.reason
                raise RuntimeError(f"Bot API {method} failed: {description}")
            return data


async def set_bot_commands_via_bot_api(bot_token: str, commands: tuple[CommandSpec, ...]) -> bool:
    if not bot_token:
        return False
    await _bot_api_post(bot_token, "setMyCommands", {"commands": build_bot_commands_payload(commands)})
    return True


async def send_ephemeral_text(
    bot_token: str,
    message,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = None,
    log=None,
) -> bool:
    user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    if not bot_token or not user or not chat or not _is_group_chat(message):
        return False

    payload = {
        "chat_id": chat.id,
        "receiver_user_id": user.id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        await _bot_api_post(bot_token, "sendMessage", payload)
        return True
    except Exception as exc:
        if reply_markup and _is_button_type_invalid(exc):
            if log:
                log.warning("Telegram rejected ephemeral Mini App button in this chat: %s", exc)
            payload.pop("reply_markup", None)
            payload["text"] = (
                text
                + "\n\nO Telegram recusou o botao do Mini App neste chat. "
                + "Abra o bot no privado e use /menu para acessar o painel."
            )
            await _bot_api_post(bot_token, "sendMessage", payload)
            return True
        if log:
            log.warning("Ephemeral message failed, falling back to regular reply: %s", exc)
        return False


async def delete_command_message(message, log=None) -> bool:
    if not _is_group_chat(message):
        return False
    try:
        await message.delete()
        return True
    except Exception as exc:
        if log:
            log.warning("Could not delete command message in group: %s", exc)
        return False


async def reply_command_menu(
    message,
    title: str,
    commands: tuple[CommandSpec, ...],
    mini_app_url: str,
    log=None,
    extra_text: str = "",
    bot_token: str = "",
    ephemeral: bool = False,
    public_fallback: bool = True,
):
    text = build_command_menu_text(title, commands, bool(mini_app_url)) + extra_text
    if ephemeral:
        await delete_command_message(message, log=log)
        ephemeral_markup = None if _is_group_chat(message) else build_mini_app_markup_payload(mini_app_url)
        sent = await send_ephemeral_text(
            bot_token,
            message,
            text,
            reply_markup=ephemeral_markup,
            log=log,
        )
        if sent:
            return None
        if not public_fallback and _is_group_chat(message):
            if log:
                log.warning("Skipping public fallback for ephemeral command menu in group chat")
            return None

    markup = build_mini_app_markup(mini_app_url)
    if not markup:
        return await message.reply_text(text)

    try:
        return await message.reply_text(text, reply_markup=markup)
    except Exception as exc:
        if not _is_button_type_invalid(exc):
            raise
        if log:
            log.warning("Telegram rejected Mini App button in this chat: %s", exc)
        fallback_text = (
            text
            + "\n\nO Telegram recusou o botao do Mini App neste chat. "
            + "Abra o bot no privado e use /menu para acessar o painel."
        )
        return await message.reply_text(fallback_text)


def mini_app_payload(kind: str, data: dict | None = None) -> str:
    return json.dumps({"kind": kind, "data": data or {}}, ensure_ascii=False)


def render_catalog_html_seed(catalog: dict) -> str:
    return escape(json.dumps(catalog, ensure_ascii=False))
