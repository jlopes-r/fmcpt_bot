import json
from html import escape

from packages.command_catalog import CommandSpec, autocomplete_commands, grouped_commands


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


def build_bot_commands(commands: tuple[CommandSpec, ...]):
    from pyrogram.types import BotCommand

    return [
        BotCommand(command.name, command.description[:60])
        for command in autocomplete_commands(commands)
    ]


def build_mini_app_markup(url: str, label: str = "Abrir painel"):
    if not url:
        return None
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, web_app=WebAppInfo(url=url))]
    ])


def _is_button_type_invalid(exc: Exception) -> bool:
    return exc.__class__.__name__ == "ButtonTypeInvalid" or "BUTTON_TYPE_INVALID" in str(exc)


async def reply_command_menu(
    message,
    title: str,
    commands: tuple[CommandSpec, ...],
    mini_app_url: str,
    log=None,
    extra_text: str = "",
):
    text = build_command_menu_text(title, commands, bool(mini_app_url)) + extra_text
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
