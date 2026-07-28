import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path

import aiohttp

from packages.command_catalog import COMANDOS_BOT_COMMANDS, SUPER_COMMANDS, CommandSpec
from packages.config import DATA_DIR, LOG_DIR, load_environment, mini_app_url, parse_chat_ids
from packages.logging_config import configure_rotating_logging
from packages.telegram_ui import (
    BOT_API_BASE,
    build_command_menu_html,
    set_bot_commands_menu_button_via_bot_api,
    set_bot_commands_via_bot_api,
)


@dataclass(frozen=True)
class BotRuntime:
    name: str
    token: str
    commands: tuple[CommandSpec, ...]
    title: str


def parse_command(text: str | None) -> tuple[str, list[str]]:
    if not text:
        return "", []
    first, *rest = text.strip().split()
    if not first.startswith("/"):
        return "", []
    return first[1:].split("@", 1)[0].lower(), rest


def split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    current = []
    current_len = 0
    for line in text.splitlines():
        projected = current_len + len(line) + 1
        if current and projected > limit:
            parts.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len = projected
    if current:
        parts.append("\n".join(current))
    return parts


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.getLogger("EphemeralCommands").exception("Failed to read %s", path)
    return default


def build_custom_command_list() -> str:
    comandos = _load_json(DATA_DIR / "comandos_personalizados.json", {})
    if not comandos:
        return "📭 Nenhum comando personalizado criado."

    tipo_emoji = {
        "texto": "📝",
        "foto": "🖼️",
        "video": "🎬",
        "audio": "🎵",
        "voice": "🎤",
        "gif": "🎞️",
    }
    tipo_nome = {
        "texto": "Texto",
        "foto": "Foto",
        "video": "Vídeo",
        "audio": "Áudio",
        "voice": "Voz",
        "gif": "GIF",
    }
    grupos: dict[str, list[str]] = {}
    for cmd, info in comandos.items():
        grupos.setdefault(info.get("tipo", "texto"), []).append(cmd)
    for nomes in grupos.values():
        nomes.sort()

    text = f"<b>📋 Comandos Personalizados:</b> {len(comandos)} no total\n\n"
    for tipo in ["video", "foto", "audio", "gif", "texto", "voice"]:
        nomes = grupos.get(tipo)
        if nomes:
            text += f"{tipo_emoji.get(tipo, '❓')} <b>{escape(tipo_nome.get(tipo, tipo))}</b> ({len(nomes)}):\n"
            text += " ".join(f"/{escape(nome)}" for nome in nomes)
            text += "\n\n"
    return text.strip()


def build_gif_stats() -> str:
    catolicos = _load_json(DATA_DIR / "gifs_catolicos.json", [])
    duvida = _load_json(DATA_DIR / "gifs_interrogacao.json", [])
    return (
        "<b>📊 Estatísticas de GIFs</b>\n\n"
        f"🙏 Instance: <code>{len(catolicos)}</code> GIFs\n"
        f"❓ Dúvida: <code>{len(duvida)}</code> GIFs"
    )


def build_backlog_list() -> str:
    backlog = _load_json(DATA_DIR / "backlog.json", [])
    if not backlog:
        return "📭 Nenhuma sugestão pendente no backlog."
    lines = ["<b>📋 Backlog pendente</b>", ""]
    for i, item in enumerate(backlog[:50], 1):
        if isinstance(item, dict):
            text = item.get("texto") or item.get("text") or json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
        lines.append(f"{i}. {escape(text)}")
    if len(backlog) > 50:
        lines.append(f"\n... e mais {len(backlog) - 50} item(ns).")
    return "\n".join(lines)


class BotApi:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"{BOT_API_BASE}{token}"

    async def post(self, session: aiohttp.ClientSession, method: str, payload: dict) -> dict:
        async with session.post(f"{self.base_url}/{method}", json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400 or not data.get("ok"):
                raise RuntimeError(f"{method} failed: {data.get('description') or response.reason}")
            return data


class EphemeralCommandService:
    def __init__(self):
        load_environment()
        configure_rotating_logging(LOG_DIR, "ephemeral_commands.log")
        self.log = logging.getLogger("EphemeralCommands")
        self.allowed_chats = set(parse_chat_ids(os.getenv("GRUPOS_AUTORIZADOS", "")))
        self.mini_app_url = mini_app_url()
        self.start_time = time.time()
        self.stop_event = asyncio.Event()
        self.bots = [
            BotRuntime("super", os.getenv("BOT_TOKEN", ""), SUPER_COMMANDS, "🤖 Guia do Super Bot"),
            BotRuntime("comandos", os.getenv("BOT_TOKEN_COMANDOS", ""), COMANDOS_BOT_COMMANDS, "🤖 Menu de comandos"),
        ]

    def is_allowed_chat(self, chat_id: int) -> bool:
        return not self.allowed_chats or chat_id in self.allowed_chats

    async def send_ephemeral(
        self,
        session: aiohttp.ClientSession,
        api: BotApi,
        chat_id: int,
        user_id: int,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        for part in split_text(text):
            payload = {
                "chat_id": chat_id,
                "receiver_user_id": user_id,
                "text": part,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            await api.post(session, "sendMessage", payload)

    async def handle_command(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        message: dict,
    ) -> None:
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        chat_type = chat.get("type")
        command, args = parse_command(message.get("text"))

        if chat_type not in {"group", "supergroup"} or not chat_id or not user_id or not command:
            return
        if not self.is_allowed_chat(int(chat_id)):
            return

        if command in {"menu", "help"}:
            extra = ""
            if runtime.name == "comandos" and _load_json(DATA_DIR / "comandos_personalizados.json", {}):
                extra = "\n\n<b>Comandos personalizados</b>\nUse <code>/list</code> ou o painel no privado para ver todos."
            text = build_command_menu_html(runtime.title, runtime.commands, bool(self.mini_app_url)) + extra
        elif runtime.name == "comandos" and command == "list":
            text = build_custom_command_list()
        elif runtime.name == "comandos" and command == "gifstats":
            text = build_gif_stats()
        elif runtime.name == "comandos" and command == "backlog":
            text = build_backlog_list() if not args else "Use o painel no privado para gerenciar o backlog."
        elif command == "id":
            text = f"🆔 ID deste Chat: <code>{chat_id}</code>"
        elif command == "stats" and runtime.name == "super":
            uptime = int(time.time() - self.start_time)
            text = f"<b>📊 Status</b>\n\n⏱️ Listener efêmero online há <code>{uptime}s</code>"
        elif command == "sync":
            await set_bot_commands_via_bot_api(runtime.token, runtime.commands)
            await set_bot_commands_menu_button_via_bot_api(runtime.token)
            text = "✅ Menu de comandos efêmeros atualizado."
        elif command in {"create", "delete", "add", "removegif", "cancelar"}:
            text = "Abra o bot no privado e use o painel para esta ação de configuração."
        else:
            return

        await self.send_ephemeral(session, api, int(chat_id), int(user_id), text, parse_mode="HTML")
        self.log.info("%s handled ephemeral /%s for user=%s chat=%s", runtime.name, command, user_id, chat_id)

    async def poll_bot(self, runtime: BotRuntime) -> None:
        if not runtime.token:
            self.log.warning("%s token is not configured; skipping", runtime.name)
            return

        api = BotApi(runtime.token)
        offset = 0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70)) as session:
            await api.post(session, "deleteWebhook", {"drop_pending_updates": False})
            await set_bot_commands_via_bot_api(runtime.token, runtime.commands)
            await set_bot_commands_menu_button_via_bot_api(runtime.token)
            self.log.info("%s ephemeral command listener started", runtime.name)

            while not self.stop_event.is_set():
                try:
                    data = await api.post(
                        session,
                        "getUpdates",
                        {
                            "offset": offset,
                            "timeout": 50,
                            "allowed_updates": ["message"],
                        },
                    )
                    for update in data.get("result", []):
                        offset = max(offset, update["update_id"] + 1)
                        message = update.get("message") or {}
                        await self.handle_command(session, runtime, api, message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.log.exception("%s polling error", runtime.name)
                    await asyncio.sleep(5)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop_event.set)

        tasks = [asyncio.create_task(self.poll_bot(bot)) for bot in self.bots]
        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(EphemeralCommandService().run())


if __name__ == "__main__":
    main()
