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
from packages.json_store import (
    load_json as _load_json_safe,
    save_json as _save_json_safe,
    update_json as _update_json_safe,
)
from packages.logging_config import configure_rotating_logging
from packages.telegram_ui import (
    BOT_API_BASE,
    build_command_menu_html,
    set_bot_commands_menu_button_via_bot_api,
    set_bot_commands_via_bot_api,
)


CUSTOM_COMMANDS_FILE = DATA_DIR / "comandos_personalizados.json"
CUSTOM_CATEGORIES_FILE = DATA_DIR / "categorias_comandos_personalizados.json"
BACKLOG_FILE = DATA_DIR / "backlog.json"
BACKLOG_TRASH_FILE = DATA_DIR / "sugestoes_de_merda.json"
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
INTERNAL_COMANDOS_COMMANDS = set(command_names(COMANDOS_BOT_COMMANDS))
DEFAULT_CUSTOM_CATEGORY = "Comandos personalizados"
CUSTOM_COMMAND_PAGE_SIZE = 35
BACKLOG_PAGE_SIZE = 10
CUSTOM_COMMAND_TYPES = ("all", "texto", "foto", "gif", "video", "audio", "voice")
CUSTOM_COMMAND_TYPE_LABELS = {
    "all": "Todos",
    "texto": "Texto",
    "foto": "Imagem",
    "gif": "GIF",
    "video": "Video",
    "audio": "Audio",
    "voice": "Voz",
}


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


def clamp_page(page: int, total_items: int, page_size: int) -> int:
    if total_items <= 0:
        return 0
    max_page = (total_items - 1) // page_size
    return max(0, min(page, max_page))


def page_items(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    safe_page = clamp_page(page, len(items), page_size)
    start = safe_page * page_size
    return items[start:start + page_size], safe_page, max(1, ((len(items) - 1) // page_size) + 1) if items else 1


def _load_json(path: Path, default):
    return _load_json_safe(path, default)


def _save_json(path: Path, data) -> None:
    _save_json_safe(path, data)


def load_custom_commands() -> dict:
    data = _load_json(CUSTOM_COMMANDS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_custom_commands(commands: dict) -> None:
    _save_json(CUSTOM_COMMANDS_FILE, commands)


def update_custom_commands(updater) -> dict:
    return _update_json_safe(CUSTOM_COMMANDS_FILE, {}, updater)


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
    def add_category(categories: list) -> None:
        if category.lower() not in {str(c).lower() for c in categories}:
            categories.append(category)

    _update_json_safe(CUSTOM_CATEGORIES_FILE, [], add_category)


def load_backlog() -> list:
    data = _load_json(BACKLOG_FILE, [])
    return data if isinstance(data, list) else []


def save_backlog(backlog: list) -> None:
    _save_json(BACKLOG_FILE, backlog)


def update_backlog(updater) -> list:
    return _update_json_safe(BACKLOG_FILE, [], updater)


def load_backlog_trash() -> list:
    data = _load_json(BACKLOG_TRASH_FILE, [])
    return data if isinstance(data, list) else []


def save_backlog_trash(items: list) -> None:
    _save_json(BACKLOG_TRASH_FILE, items)


def backlog_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("sugestao") or item.get("texto") or item.get("text") or json.dumps(item, ensure_ascii=False))
    return str(item)


def backlog_item_id(item, index: int) -> int:
    if isinstance(item, dict) and isinstance(item.get("id"), int):
        return item["id"]
    return index + 1


def next_backlog_id(backlog: list) -> int:
    return max((item.get("id", 0) for item in backlog if isinstance(item, dict)), default=0) + 1


def build_backlog_item(text: str, user: dict, backlog: list | None = None) -> dict:
    backlog = backlog if backlog is not None else load_backlog()
    return {
        "id": next_backlog_id(backlog),
        "sugestao": text,
        "autor": user.get("first_name") or user.get("username") or "Anonimo",
        "autor_id": user.get("id") or 0,
        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "origem": "ephemeral_group",
    }


def match_backlog_items(backlog: list, query: str) -> tuple[list, list[str]]:
    tokens = [item.strip().lower() for item in query.split(",") if item.strip()]
    matched: list = []
    failures: list[str] = []
    for token in tokens:
        found = []
        if token.isdigit():
            wanted = int(token)
            found = [item for index, item in enumerate(backlog) if backlog_item_id(item, index) == wanted]
        else:
            found = [item for item in backlog if token in backlog_text(item).lower()]
        if not found:
            failures.append(f"'{token}' não encontrado")
        elif len(found) > 1:
            failures.append(f"'{token}' encontrou múltiplos itens")
        elif found[0] not in matched:
            matched.append(found[0])
    return matched, failures


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


def custom_command_type(info: dict) -> str:
    command_type = str(info.get("tipo") or "texto").lower()
    if command_type in {"imagem", "image"}:
        return "foto"
    return command_type if command_type in CUSTOM_COMMAND_TYPES else "texto"


def build_custom_command_list_view(command_type: str = "all", page: int = 0) -> tuple[str, dict | None]:
    comandos = load_custom_commands()
    if not comandos:
        return "📭 Nenhum comando personalizado criado.", inline_keyboard([("Criar comando", "create:start")])

    selected_type = command_type if command_type in CUSTOM_COMMAND_TYPES else "all"
    all_rows = sorted(
        (name, custom_command_type(info if isinstance(info, dict) else {}))
        for name, info in comandos.items()
    )
    rows = all_rows
    if selected_type != "all":
        rows = [row for row in rows if row[1] == selected_type]

    visible, safe_page, total_pages = page_items(rows, page, CUSTOM_COMMAND_PAGE_SIZE)
    counts = {
        item_type: sum(1 for _, row_type in all_rows if row_type == item_type)
        for item_type in CUSTOM_COMMAND_TYPES
        if item_type != "all"
    }
    title = CUSTOM_COMMAND_TYPE_LABELS[selected_type]
    lines = [
        f"<b>📋 Comandos personalizados</b>",
        f"Filtro: <b>{escape(title)}</b> • {len(rows)} de {len(comandos)} comando(s)",
        f"Página {safe_page + 1}/{total_pages}",
        "",
    ]
    if visible:
        lines.append(" ".join(f"/{escape(name)}" for name, _ in visible))
    else:
        lines.append("Nenhum comando neste filtro.")

    filter_rows = [
        [
            ("Todos", "list:all:0"),
            (f"Texto ({counts.get('texto', 0)})", "list:texto:0"),
        ],
        [
            (f"Imagem ({counts.get('foto', 0)})", "list:foto:0"),
            (f"GIF ({counts.get('gif', 0)})", "list:gif:0"),
        ],
        [
            (f"Vídeo ({counts.get('video', 0)})", "list:video:0"),
            (f"Áudio ({counts.get('audio', 0)})", "list:audio:0"),
            (f"Voz ({counts.get('voice', 0)})", "list:voice:0"),
        ],
    ]
    nav_row = []
    if safe_page > 0:
        nav_row.append(("Anterior", f"list:{selected_type}:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav_row.append(("Próxima", f"list:{selected_type}:{safe_page + 1}"))
    rows_markup = filter_rows + ([nav_row] if nav_row else []) + [[("Criar comando", "create:start")]]
    return "\n".join(lines), inline_keyboard(*rows_markup)


def build_custom_command_list() -> str:
    text, _ = build_custom_command_list_view()
    return text


def build_gif_stats() -> str:
    catolicos = _load_json(DATA_DIR / "gifs_catolicos.json", [])
    duvida = _load_json(DATA_DIR / "gifs_interrogacao.json", [])
    return (
        "<b>📊 Estatísticas de GIFs</b>\n\n"
        f"🙏 Instance: <code>{len(catolicos)}</code> GIFs\n"
        f"❓ Dúvida: <code>{len(duvida)}</code> GIFs"
    )


def build_backlog_view(page: int = 0, trash: bool = False) -> tuple[str, dict | None]:
    backlog = load_backlog_trash() if trash else load_backlog()
    title = "🗑️ Backlog descartado" if trash else "📋 Backlog pendente"
    if not backlog:
        keyboard = inline_keyboard([("Adicionar", "backlog:add")], [("Pendentes", "backlog:view:0")]) if trash else inline_keyboard([("Adicionar", "backlog:add")])
        return f"📭 Nenhuma sugestão {'descartada' if trash else 'pendente'} no backlog.", keyboard

    visible, safe_page, total_pages = page_items(list(enumerate(backlog)), page, BACKLOG_PAGE_SIZE)
    lines = [f"<b>{escape(title)}</b>", f"Página {safe_page + 1}/{total_pages} • {len(backlog)} item(ns)", ""]
    for index, item in visible:
        item_id = backlog_item_id(item, index)
        lines.append(f"{item_id}. {escape(backlog_text(item))}")

    nav_row = []
    prefix = "backlog:trash" if trash else "backlog:view"
    if safe_page > 0:
        nav_row.append(("Anterior", f"{prefix}:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav_row.append(("Próxima", f"{prefix}:{safe_page + 1}"))

    if trash:
        rows = ([nav_row] if nav_row else []) + [[("Pendentes", "backlog:view:0")]]
    else:
        rows = (
            ([nav_row] if nav_row else [])
            + [
                [("Adicionar", "backlog:add"), ("Concluir", "backlog:done")],
                [("Mover p/ lixeira", "backlog:trashmove"), ("Ver lixeira", "backlog:trash:0")],
                [("Limpar tudo", "backlog:clear")],
            ]
        )
    return "\n".join(lines), inline_keyboard(*rows)


def build_backlog_list() -> str:
    text, _ = build_backlog_view()
    return text


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
        self.backlog_states: dict[tuple[str, int, int], dict] = {}
        self.pending_deletes: dict[tuple[str, int, int], str] = {}

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

    async def edit_or_send_ephemeral(
        self,
        session: aiohttp.ClientSession,
        api: BotApi,
        chat_id: int,
        user_id: int,
        text: str,
        callback_query_id: str,
        ephemeral_message_id: int | None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        if ephemeral_message_id:
            payload = {
                "chat_id": chat_id,
                "receiver_user_id": user_id,
                "ephemeral_message_id": ephemeral_message_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup:
                payload["reply_markup"] = reply_markup
            try:
                await api.post(session, "editEphemeralMessageText", payload)
                return
            except Exception as exc:
                self.log.warning("editEphemeralMessageText failed, sending a new ephemeral message: %s", exc)

        await self.send_ephemeral(
            session,
            api,
            chat_id,
            user_id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            callback_query_id=callback_query_id,
        )

    def state_key(self, runtime: BotRuntime, chat_id: int, user_id: int) -> tuple[str, int, int]:
        return runtime.name, chat_id, user_id

    def clear_user_states(self, runtime: BotRuntime, chat_id: int, user_id: int) -> None:
        key = self.state_key(runtime, chat_id, user_id)
        self.create_states.pop(key, None)
        self.backlog_states.pop(key, None)

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
        self.pending_deletes[("comandos", chat_id, user_id)] = key
        await self.send_ephemeral(
            session,
            api,
            chat_id,
            user_id,
            f"Confirma apagar /{escape(key)}?",
            reply_markup=inline_keyboard(
                [("Confirmar exclusão", f"delete:confirm:{key}")],
                [("Cancelar", "delete:cancel")],
            ),
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
            command_name = data["name"]
            record = {
                "tipo": data.get("type", "texto"),
                "conteudo": data.get("content", ""),
                "media_id": data.get("media_id"),
                "descricao": text[:100],
                "categoria": DEFAULT_CUSTOM_CATEGORY,
                "criado_por": user_id,
                "data_criacao": str(datetime.now()),
                "origem": "ephemeral_group",
            }
            update_custom_commands(lambda commands: commands.__setitem__(command_name, record))
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

    async def handle_backlog_state(
        self,
        session: aiohttp.ClientSession,
        runtime: BotRuntime,
        api: BotApi,
        message: dict,
    ) -> bool:
        chat_id = int((message.get("chat") or {}).get("id"))
        user = message.get("from") or {}
        user_id = int(user.get("id"))
        key = self.state_key(runtime, chat_id, user_id)
        state = self.backlog_states.get(key)
        if not state:
            return False

        text = message_text(message)
        command, _ = parse_command(text)
        if command == "cancelar":
            self.backlog_states.pop(key, None)
            await self.send_ephemeral(session, api, chat_id, user_id, "❌ Ação de backlog cancelada.")
            return True

        action = state.get("action")
        if action == "add":
            if not text:
                await self.send_ephemeral(session, api, chat_id, user_id, "Envie uma sugestão com texto.")
                return True
            created: dict[str, dict] = {}

            def add_backlog_item(backlog: list) -> None:
                item = build_backlog_item(text, user, backlog)
                backlog.append(item)
                created["item"] = item

            update_backlog(add_backlog_item)
            item = created["item"]
            self.backlog_states.pop(key, None)
            view_text, markup = build_backlog_view()
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                f"✅ Sugestão #{item['id']} adicionada.\n\n{view_text}",
                parse_mode="HTML",
                reply_markup=markup,
            )
            return True

        if action in {"done", "trashmove"}:
            backlog = load_backlog()
            matched, failures = match_backlog_items(backlog, text)
            if not matched:
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    "Nenhum item encontrado. Envie IDs ou trechos separados por vírgula, ou use /cancelar.",
                )
                return True
            remaining = [item for item in backlog if item not in matched]
            save_backlog(remaining)
            if action == "trashmove":
                trash = load_backlog_trash()
                trash.extend(matched)
                save_backlog_trash(trash)
            self.backlog_states.pop(key, None)
            verb = "concluído(s)" if action == "done" else "movido(s) para a lixeira"
            suffix = "\nFalhas: " + "; ".join(failures) if failures else ""
            view_text, markup = build_backlog_view()
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                f"✅ {len(matched)} item(ns) {verb}.{suffix}\n\n{view_text}",
                parse_mode="HTML",
                reply_markup=markup,
            )
            return True

        self.backlog_states.pop(key, None)
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
        ephemeral_message_id = message.get("ephemeral_message_id")
        chat_id = chat.get("id")
        user_id = user.get("id")
        chat_type = chat.get("type")
        if chat_type not in {"group", "supergroup"} or not chat_id or not user_id or not callback_id:
            return
        chat_id = int(chat_id)
        user_id = int(user_id)
        if not self.is_allowed_chat(chat_id):
            return
        if not data.startswith(("create:", "delete:", "list:", "backlog:")):
            return

        await api.post(session, "answerCallbackQuery", {"callback_query_id": callback_id})
        key = self.state_key(runtime, chat_id, user_id)

        if data == "create:start":
            await self.start_create_flow(session, runtime, api, chat_id, user_id)
            return

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
            return

        if data == "delete:cancel":
            self.pending_deletes.pop(key, None)
            await self.send_ephemeral(session, api, chat_id, user_id, "Exclusão cancelada.", callback_query_id=callback_id)
            return

        if data.startswith("delete:confirm:"):
            command_name = data.split(":", 2)[2]
            pending = self.pending_deletes.get(key)
            if pending != command_name:
                await self.send_ephemeral(session, api, chat_id, user_id, "Esta confirmação expirou.", callback_query_id=callback_id)
                return
            deleted: dict[str, str] = {}

            def delete_command(commands: dict) -> None:
                real_key = find_custom_command_key(commands, command_name)
                if real_key:
                    deleted["name"] = real_key
                    del commands[real_key]

            update_custom_commands(delete_command)
            real_key = deleted.get("name")
            if not real_key:
                self.pending_deletes.pop(key, None)
                await self.send_ephemeral(session, api, chat_id, user_id, f"Comando /{command_name} não encontrado.", callback_query_id=callback_id)
                return
            self.pending_deletes.pop(key, None)
            await set_bot_commands_via_bot_api(api.token, COMANDOS_BOT_COMMANDS)
            await set_bot_commands_menu_button_via_bot_api(api.token)
            await self.send_ephemeral_with_commands(session, api, chat_id, user_id, f"✅ Comando /{real_key} apagado.")
            return

        if data.startswith("list:"):
            _, command_type, page_raw = data.split(":", 2)
            text, markup = build_custom_command_list_view(command_type, int(page_raw) if page_raw.isdigit() else 0)
            await self.edit_or_send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                text,
                callback_id,
                ephemeral_message_id,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return

        if data.startswith("backlog:view:"):
            page_raw = data.rsplit(":", 1)[1]
            text, markup = build_backlog_view(int(page_raw) if page_raw.isdigit() else 0)
            await self.edit_or_send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                text,
                callback_id,
                ephemeral_message_id,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return

        if data.startswith("backlog:trash:"):
            page_raw = data.rsplit(":", 1)[1]
            text, markup = build_backlog_view(int(page_raw) if page_raw.isdigit() else 0, trash=True)
            await self.edit_or_send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                text,
                callback_id,
                ephemeral_message_id,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return

        if data == "backlog:add":
            self.backlog_states[key] = {"action": "add", "started_at": time.time()}
            await self.send_ephemeral(session, api, chat_id, user_id, "Envie o texto da nova sugestão. Use /cancelar para desistir.", callback_query_id=callback_id)
            return

        if data in {"backlog:done", "backlog:trashmove"}:
            action = "done" if data == "backlog:done" else "trashmove"
            self.backlog_states[key] = {"action": action, "started_at": time.time()}
            label = "concluir" if action == "done" else "mover para a lixeira"
            await self.send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                f"Envie os IDs ou trechos dos itens para {label}, separados por vírgula. Use /cancelar para desistir.",
                callback_query_id=callback_id,
            )
            return

        if data == "backlog:clear":
            await self.edit_or_send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                "Confirma mover todo o backlog pendente para a lixeira?",
                callback_id,
                ephemeral_message_id,
                reply_markup=inline_keyboard([("Confirmar limpeza", "backlog:clear:confirm")], [("Cancelar", "backlog:view:0")]),
            )
            return

        if data == "backlog:clear:confirm":
            backlog = load_backlog()
            if backlog:
                trash = load_backlog_trash()
                trash.extend(backlog)
                save_backlog_trash(trash)
                save_backlog([])
            text, markup = build_backlog_view()
            await self.edit_or_send_ephemeral(
                session,
                api,
                chat_id,
                user_id,
                f"✅ {len(backlog)} item(ns) movido(s) para a lixeira.\n\n{text}",
                callback_id,
                ephemeral_message_id,
                parse_mode="HTML",
                reply_markup=markup,
            )

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

        if runtime.name == "comandos" and self.state_key(runtime, chat_id, user_id) in self.backlog_states:
            if await self.handle_backlog_state(session, runtime, api, message):
                return

        if not command:
            return

        if command in {"menu", "help"}:
            extra = ""
            if runtime.name == "comandos" and load_custom_commands():
                extra = "\n\n<b>Comandos personalizados</b>\nUse <code>/list</code> ou o painel no privado para ver todos."
            text = build_command_menu_html(runtime.title, runtime.commands, bool(self.mini_app_url)) + extra
        elif runtime.name == "comandos" and command == "list":
            text, markup = build_custom_command_list_view()
            await self.send_ephemeral(session, api, chat_id, user_id, text, parse_mode="HTML", reply_markup=markup)
            self.log.info("%s handled ephemeral /list for user=%s chat=%s", runtime.name, user_id, chat_id)
            return
        elif runtime.name == "comandos" and command == "gifstats":
            text = build_gif_stats()
        elif runtime.name == "comandos" and command == "backlog":
            if args:
                created: dict[str, dict] = {}

                def add_backlog_item(backlog: list) -> None:
                    item = build_backlog_item(" ".join(args), user, backlog)
                    backlog.append(item)
                    created["item"] = item

                update_backlog(add_backlog_item)
                item = created["item"]
                text, markup = build_backlog_view()
                await self.send_ephemeral(
                    session,
                    api,
                    chat_id,
                    user_id,
                    f"✅ Sugestão #{item['id']} adicionada.\n\n{text}",
                    parse_mode="HTML",
                    reply_markup=markup,
                )
            else:
                text, markup = build_backlog_view()
                await self.send_ephemeral(session, api, chat_id, user_id, text, parse_mode="HTML", reply_markup=markup)
            self.log.info("%s handled ephemeral /backlog for user=%s chat=%s", runtime.name, user_id, chat_id)
            return
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
