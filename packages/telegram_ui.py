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


def mini_app_payload(kind: str, data: dict | None = None) -> str:
    return json.dumps({"kind": kind, "data": data or {}}, ensure_ascii=False)


def render_catalog_html_seed(catalog: dict) -> str:
    return escape(json.dumps(catalog, ensure_ascii=False))
