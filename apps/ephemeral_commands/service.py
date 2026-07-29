import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

import aiohttp

from packages.command_catalog import COMANDOS_BOT_COMMANDS, SUPER_COMMANDS, CommandSpec, command_names
from packages.config import DATA_DIR, LOG_DIR, load_environment, mini_app_url, parse_chat_ids
from packages.logging_config import configure_rotating_logging
from packages.telegram_ui import (
    BOT_API_BASE,
    build_command_menu_html,
    set_bot_commands_menu_button_via_bot_api,
    set_bot_commands_via_bot_api,
)


CUSTOM_COMMANDS_FILE = DATA_DIR / "comandos_personalizados.json"
CUSTOM_CATEGORIES_FILE = DATA_DIR / "categorias_comandos_personalizados.json"
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
INTERNAL_COMANDOS_COMMANDS = set(command_names(COMANDOS_BOT_COMMANDS))
DEFAULT_CUSTOM_CATEGORY = "Comandos personalizados"


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


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def bot_command_entities(text: str) -> list[dict]:
    entities = []
    for match in re.finditer(r"(?<!\S)/[A-Za-z0-9_]{1,32}(?:@[A-Za-z0-9_]{5,32})?", text):
        entities.append({
            "type": "bot_command",
            "offset": utf16_len(text[:match.start()]),
            "length": utf16_len(match.group(0)),
        })
    return entities


def inline_keyboard(*rows: list[tuple[str, str]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def cancel_keyboard() -> dict:
    return inline_keyboard([("Cancelar", "create:cancel")])


def type_keyboard() -> dict:
    return inline_keyboard(
        [("Texto", "create:type:text")],
        [("Cancelar", "create:cancel")],
    )


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.getLogger("EphemeralCommands").exception("Failed to read %s", path)
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_custom_commands() -> dict:
    data = _load_json(CUSTOM_COMMANDS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_custom_commands(commands: dict) -> None:
    _save_json(CUSTOM_COMMANDS_FILE, commands)


def load_custom_categories() -> list[str]:
    data = _load_json(CUSTOM_CATEGORIES_FILE, [])
    if not isinstance(data, list):
        return []
    categories: list[str] = []
    for item in data:
        category = str(item).strip()
        if category and category.lower() not in {c.lower() for c in categories}:
            categories.append(category)
    return categories


def save_custom_categories(categories: list[str]) -> None:
    unique: list[str] = []
    for item in categories:
        category = str(item).strip()
        if category and category.lower() not in {c.lower() for c in unique}:
            unique.append(category)
    _save_json(CUSTOM_CATEGORIES_FILE, unique)


def ensure_custom_category(category: str = DEFAULT_CUSTOM_CATEGORY) -> None:
    categories = load_custom_categories()
    if category.lower() not in {c.lower() for c in categories}:
        categories.append(category)
        save_custom_categories(categories)


def find_custom_command_key(commands: dict, name: str) -> str | None:
    normalized = name.strip().lstrip("/").lower()
    return next((key for key in commands if key.lower() == normalized), None)


def validate_custom_command_name(name: str) -> tuple[str | None, str | None]:
    normalized = name.strip().lstrip("/").lower()
    if not COMMAND_NAME_RE.match(normalized):
        return None, "Nome inválido. Use letras minúsculas, números ou underline, até 32 caracteres."
    if normalized in INTERNAL_COMANDOS_COMMANDS:
        return None, f"/{escape(normalized)} já é um comando interno do bot."
    return normalized, None


def message_text(message: dict) -> str:
    return str(message.get("text") or message.get("caption") or "").strip()


def extract_media_command_data(message: dict) -> tuple[str, str, str] | None:
    caption = str(message.get("caption") or "").strip()
    if photos := message.get("photo"):
        photo = photos[-1] if isinstance(photos, list) and photos else {}
        if file_id := photo.get("file_id"):
            return "foto", file_id, caption
    for field, command_type in (
        ("animation", "gif"),
        ("video", "video"),
        ("audio", "audio"),
        ("voice", "voice"),
    ):
        media = message.get(field) or {}
        if file_id := media.get("file_id"):
            return command_type, file_id, caption

    document = message.get("document") or {}
    file_id = document.get("file_id")
    mime_type = str(document.get("mime_type") or "").lower()
    if not file_id:
        return None
    if mime_type == "image/gif":
        return "gif", file_id, caption
    if mime_type.startswith("image/"):
        return "foto", file_id, caption
    if mime_type.startswith("video/"):
        return "video", file_id, caption
    if mime_type.startswith("audio/"):
        return "audio", file_id, caption
    return None


def build_custom_command_list() -> str:
    comandos = load_custom_commands()
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
        self.create_states: dict[tuple[str, int, int], dict] = {}

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
        entities: list[dict] | None = None,
        reply_markup: dict | None = None,
        callback_query_id: str | None = None,
    ) -> None:
        parts = split_text(text)
        for part in parts:
            payload = {
                "chat_id": chat_id,
                "receiver_user_id": user_id,
                "text": part,
            }
            if callback_query_id:
                payload["callback_query_id"] = callback_query_id
            if entities and len(parts) == 1:
                payload["entities"] = entities
            elif parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup and len(parts) == 1:
                payload["reply_markup"] = reply_markup
            await api.post(session, "sendMessage", payload)

    async def send_ephemeral_with_commands(
        self,
        session: aiohttp.ClientSession,
        api: BotApi,
        chat_id: int,
        user_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        await self.send_ephemeral(
            session,
            api,
            chat_id,
            user_id,
            text,
            entities=bot_command_entities(text),
            reply_markup=reply_markup,
        )

    def state_key(self, runtime: BotRuntime, chat_id: int, user_id: int) -> tuple[str, int, int]:
        return runtime.name, chat_id, user_id

    async def start_create_flow(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        chat_id: int,
        user_id: int,
    ) -> None:
        self.create_states[self.state_key(runtime, chat_id, user_id)] = {
            "step": "name",
            "data": {},
            "started_at": time.time(),
        }
        await self.send_ephemeral_with_commands(
            session,
            api,
            chat_id,
            user_id,
            (
                "📝 Criação de comando personalizado\n\n"
                "Digite o nome do comando sem a barra.\n"
                "Exemplo: frias\n\n"
                "Use o botão abaixo para desistir."
            ),
            reply_markup=cancel_keyboard(),
        )

    async def cancel_create_flow(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        chat_id: int,
        user_id: int,
    ) -> None:
        key = self.state_key(runtime, chat_id, user_id)
        existed = self.create_states.pop(key, None) is not None
        text = "❌ Criação de comando cancelada." if existed else "Não há criação em andamento."
        await self.send_ephemeral(session, api, chat_id, user_id, text, parse_mode="HTML")

    async def delete_custom_command(
        self,
        session: aiohttp.ClientSession,
        api: BotApi,
        chat_id: int,
        user_id: int,
        args: list[str],
    ) -> None:
        if not args:
            await self.send_ephemeral_with_commands(
                session,
                api,
                chat_id,
                user_id,
                "Uso: /delete nome_do_comando",
            )
            return

        command_name, error = validate_custom_command_name(args[0])
        if error and command_name is None:
            await self.send_ephemeral(session, api, chat_id, user_id, f"❌ {error}", parse_mode="HTML")
            return

        commands = load_custom_commands()
        key = find_custom_command_key(commands, command_name or args[0])
        if not key:
            await self.send_ephemeral_with_commands(
                session,
                api,
                chat_id,
                user_id,
                f"❌ Comando /{command_name or args[0]} não encontrado.",
            )
            return
        del commands[key]
        save_custom_commands(commands)
        await set_bot_commands_via_bot_api(api.token, COMANDOS_BOT_COMMANDS)
        await set_bot_commands_menu_button_via_bot_api(api.token)
        await self.send_ephemeral_with_commands(
            session,
            api,
            chat_id,
            user_id,
            f"✅ Comando /{key} apagado.",
        )

    async def handle_create_state(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        message: dict,
    ) -> bool:
        chat_id = int((message.get("chat") or {}).get("id"))
        user_id = int((message.get("from") or {}).get("id"))
        key = self.state_key(runtime, chat_id, user_id)
        state = self.create_states.get(key)
        if not state:
            return False

        text = message_text(message)
        command, _ = parse_command(text)
        if command == "cancelar":
            await self.cancel_create_flow(session, runtime, api, chat_id, user_id)
            return True

        step = state["step"]
        data = state["data"]

        if step == "name":
            name, error = validate_custom_command_name(text)
            if error:
                await self.send_ephemeral(session, api, chat_id, user_id, f"❌ {error}", parse_mode="HTML")
                return True

            commands = load_custom_commands()
            data["name"] = name
            state["step"] = "type"
            warning = ""
            if find_custom_command_key(commands, name):
                warning = "\n\n⚠️ Este comando já existe e será substituído se você continuar."
            await self.send_ephemeral_with_commands(
                session,
                api,
                chat_id,
                user_id,
                (
                    f"✅ Comando /{name} definido.{warning}\n\n"
                    "Agora escolha o tipo:\n"
                    "• toque em Texto para criar uma resposta textual;\n"
                    "• ou envie foto, GIF, vídeo, áudio ou voz com legenda opcional."
                ),
                reply_markup=type_keyboard(),
            )
            return True

        if step == "type":
            if text.lower() == "texto":
                data["type"] = "texto"
                state["step"] = "text_content"
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    "✅ Tipo texto definido. Agora envie o conteúdo do comando.",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard(),
                )
                return True

            media = extract_media_command_data(message)
            if media:
                data["type"], data["media_id"], data["content"] = media
                state["step"] = "description"
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    "✅ Mídia recebida. Agora envie uma descrição curta para aparecer no catálogo.",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard(),
                )
                return True

            await self.send_ephemeral_with_commands(
                session,
                api,
                chat_id,
                user_id,
                "Envie uma mídia válida: foto, GIF, vídeo, áudio ou voz. Para texto, toque no botão Texto.",
                reply_markup=type_keyboard(),
            )
            return True

        if step == "text_content":
            if not text:
                await self.send_ephemeral(session, api, chat_id, user_id, "O conteúdo não pode ser vazio.")
                return True
            data["content"] = text
            state["step"] = "description"
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                "✅ Conteúdo definido. Agora envie uma descrição curta para aparecer no catálogo.",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            return True

        if step == "description":
            if not text:
                await self.send_ephemeral(session, api, chat_id, user_id, "A descrição não pode ser vazia.")
                return True
            commands = load_custom_commands()
            command_name = data["name"]
            commands[command_name] = {
                "tipo": data.get("type", "texto"),
                "conteudo": data.get("content", ""),
                "media_id": data.get("media_id"),
                "descricao": text[:100],
                "categoria": DEFAULT_CUSTOM_CATEGORY,
                "criado_por": user_id,
                "data_criacao": str(datetime.now()),
                "origem": "ephemeral_group",
            }
            save_custom_commands(commands)
            ensure_custom_category(DEFAULT_CUSTOM_CATEGORY)
            await set_bot_commands_via_bot_api(api.token, COMANDOS_BOT_COMMANDS)
            await set_bot_commands_menu_button_via_bot_api(api.token)
            self.create_states.pop(key, None)
            await self.send_ephemeral_with_commands(
                session,
                api,
                chat_id,
                user_id,
                (
                    "✅ Comando criado com sucesso.\n\n"
                    f"Use /{command_name} no grupo para testar."
                ),
            )
            self.log.info("created custom command /%s via ephemeral flow user=%s chat=%s", command_name, user_id, chat_id)
            return True

        self.create_states.pop(key, None)
        return False

    async def handle_callback_query(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        callback_query: dict,
    ) -> None:
        if runtime.name != "comandos":
            return

        callback_id = callback_query.get("id")
        data = str(callback_query.get("data") or "")
        user = callback_query.get("from") or {}
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        chat_type = chat.get("type")
        if chat_type not in {"group", "supergroup"} or not chat_id or not user_id or not callback_id:
            return
        chat_id = int(chat_id)
        user_id = int(user_id)
        if not self.is_allowed_chat(chat_id):
            return
        if not data.startswith("create:"):
            return

        await api.post(session, "answerCallbackQuery", {"callback_query_id": callback_id})
        key = self.state_key(runtime, chat_id, user_id)

        if data == "create:cancel":
            existed = self.create_states.pop(key, None) is not None
            text = "❌ Criação de comando cancelada." if existed else "Não há criação em andamento."
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                text,
                callback_query_id=callback_id,
            )
            self.log.info("%s handled ephemeral create cancel callback user=%s chat=%s", runtime.name, user_id, chat_id)
            return

        if data == "create:type:text":
            state = self.create_states.get(key)
            if not state:
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    "Não há criação em andamento.",
                    callback_query_id=callback_id,
                )
                return
            if state.get("step") != "type":
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    "Este botão não vale mais para a etapa atual.",
                    callback_query_id=callback_id,
                )
                return
            state["data"]["type"] = "texto"
            state["step"] = "text_content"
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                "✅ Tipo texto definido. Agora envie o conteúdo do comando.",
                reply_markup=cancel_keyboard(),
                callback_query_id=callback_id,
            )
            self.log.info("%s handled ephemeral create text callback user=%s chat=%s", runtime.name, user_id, chat_id)

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

        if chat_type not in {"group", "supergroup"} or not chat_id or not user_id:
            return
        if not self.is_allowed_chat(int(chat_id)):
            return
        chat_id = int(chat_id)
        user_id = int(user_id)

        if runtime.name == "comandos" and self.state_key(runtime, chat_id, user_id) in self.create_states:
            if await self.handle_create_state(session, runtime, api, message):
                return

        if not command:
            return

        if command in {"menu", "help"}:
            extra = ""
            if runtime.name == "comandos" and load_custom_commands():
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
        elif runtime.name == "comandos" and command == "create":
            await self.start_create_flow(session, runtime, api, chat_id, user_id)
            self.log.info("%s started ephemeral /create for user=%s chat=%s", runtime.name, user_id, chat_id)
            return
        elif runtime.name == "comandos" and command == "delete":
            await self.delete_custom_command(session, api, chat_id, user_id, args)
            self.log.info("%s handled ephemeral /delete for user=%s chat=%s", runtime.name, user_id, chat_id)
            return
        elif runtime.name == "comandos" and command == "cancelar":
            await self.cancel_create_flow(session, runtime, api, chat_id, user_id)
            self.log.info("%s handled ephemeral /cancelar for user=%s chat=%s", runtime.name, user_id, chat_id)
            return
        elif command in {"add", "removegif"}:
            text = "Abra o bot no privado e use o painel para esta ação de configuração."
        else:
            return

        await self.send_ephemeral(session, api, chat_id, user_id, text, parse_mode="HTML")
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
                            "allowed_updates": ["message", "callback_query"],
                        },
                    )
                    for update in data.get("result", []):
                        offset = max(offset, update["update_id"] + 1)
                        if callback_query := update.get("callback_query"):
                            await self.handle_callback_query(session, runtime, api, callback_query)
                        else:
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
