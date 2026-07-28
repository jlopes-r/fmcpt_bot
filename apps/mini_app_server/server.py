import argparse
from datetime import datetime
import hashlib
import hmac
import json
import mimetypes
import re
import os
import time
import uuid
from collections import defaultdict, deque
from aiohttp.web_request import FileField
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import ClientSession, web

from packages.config import DATA_DIR, load_environment, parse_chat_ids


ROOT = Path(__file__).resolve().parents[2]
MINI_APP_DIR = ROOT / "apps" / "mini_app"
CATALOG_FILE = MINI_APP_DIR / "catalog.json"
COMANDOS_FILE = DATA_DIR / "comandos_personalizados.json"
CUSTOM_CATEGORIES_FILE = DATA_DIR / "categorias_comandos_personalizados.json"
BACKLOG_FILE = DATA_DIR / "backlog.json"
UPLOADS_DIR = DATA_DIR / "custom_command_uploads"
PREVIEW_CACHE_DIR = DATA_DIR / "custom_command_previews"
DEFAULT_AUTH_MAX_AGE = 2 * 60 * 60
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
API_RATE_LIMIT_REQUESTS = 60
API_RATE_LIMIT_WINDOW = 60
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
ALLOWED_COMMAND_TYPES = {"texto", "foto", "video", "audio", "voice", "gif"}
UPLOAD_EXTENSIONS = {
    "foto": ".jpg",
    "gif": ".gif",
    "video": ".mp4",
    "audio": ".mp3",
    "voice": ".ogg",
}
MEDIA_KEY_RE = re.compile(r"^[a-f0-9]{32}\.[A-Za-z0-9]{1,12}$")
PRIVATE_MEDIA_FIELDS = {
    "media_id",
    "media_path",
    "mediaUrl",
    "media_url",
    "previewUrl",
    "preview_url",
    "thumbnailUrl",
    "thumbnail_url",
    "posterUrl",
    "poster_url",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
COMMAND_MEDIA_TYPES = {
    "foto": "image/jpeg",
    "gif": "image/gif",
    "video": "video/mp4",
    "audio": "audio/mpeg",
    "voice": "audio/ogg",
}
UPLOAD_CONTENT_TYPES = {
    "foto": {"image/jpeg", "image/png", "image/webp"},
    "gif": {"image/gif"},
    "video": {"video/mp4", "video/quicktime", "video/webm"},
    "audio": {"audio/mpeg", "audio/mp3", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav", "audio/x-wav"},
    "voice": {"audio/ogg", "audio/opus", "application/ogg"},
}
UPLOAD_MAGIC_EXTENSIONS = {
    "jpeg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "gif": ".gif",
    "mp4": ".mp4",
    "webm": ".webm",
    "mp3": ".mp3",
    "ogg": ".ogg",
    "wav": ".wav",
}
UPLOAD_MAGIC_TYPES = {
    "foto": {"jpeg", "png", "webp"},
    "gif": {"gif"},
    "video": {"mp4", "webm"},
    "audio": {"mp3", "mp4", "ogg", "wav"},
    "voice": {"ogg"},
}
_rate_limit_buckets: dict[tuple[str, str, int], deque[float]] = defaultdict(deque)


def bot_tokens() -> list[str]:
    tokens = []
    for name in ("BOT_TOKEN", "BOT_TOKEN_COMANDOS"):
        token = os.getenv(name, "").strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def authorized_chat_ids() -> list[int]:
    return parse_chat_ids(os.getenv("GRUPOS_AUTORIZADOS", ""))


def auth_max_age() -> int:
    try:
        return int(os.getenv("MINI_APP_AUTH_MAX_AGE", str(DEFAULT_AUTH_MAX_AGE)))
    except ValueError:
        return DEFAULT_AUTH_MAX_AGE


def rate_limit_config() -> tuple[int, int]:
    try:
        requests = int(os.getenv("MINI_APP_RATE_LIMIT_REQUESTS", str(API_RATE_LIMIT_REQUESTS)))
        window = int(os.getenv("MINI_APP_RATE_LIMIT_WINDOW", str(API_RATE_LIMIT_WINDOW)))
    except ValueError:
        return API_RATE_LIMIT_REQUESTS, API_RATE_LIMIT_WINDOW
    return max(1, requests), max(1, window)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def default_custom_category() -> str:
    return "Comandos personalizados"


def normalize_command_name(value: str) -> str:
    return str(value or "").strip().lstrip("/").lower()


def normalize_command_type(value: str | None) -> str:
    value = str(value or "texto").strip().lower()
    return value if value in ALLOWED_COMMAND_TYPES else "texto"


def category_exists(categories: list[str], name: str) -> str | None:
    return next((item for item in categories if item.lower() == name.lower()), None)


def save_categories(categories: list[str]) -> None:
    unique = []
    seen = set()
    for category in categories:
        category = str(category or "").strip()
        key = category.lower()
        if category and key not in seen:
            unique.append(category)
            seen.add(key)
    save_json(CUSTOM_CATEGORIES_FILE, unique)


def load_catalog_payload() -> dict:
    catalog = load_json(CATALOG_FILE, {"bots": []})
    custom_commands = load_json(COMANDOS_FILE, {})
    custom_categories = load_json(CUSTOM_CATEGORIES_FILE, [])
    for bot in catalog.get("bots", []):
        if bot.get("id") == "comandos":
            bot["customCommands"] = {
                name: command_for_catalog(name, info)
                for name, info in custom_commands.items()
                if isinstance(info, dict)
            }
            bot["customCategories"] = custom_categories
            break
    return catalog


def command_for_catalog(name: str, info: dict) -> dict:
    visible = {
        key: value
        for key, value in info.items()
        if key not in PRIVATE_MEDIA_FIELDS
    }
    media_path = private_media_path(info)
    is_private_media = bool(media_path)
    has_media_id = bool(info.get("media_id"))
    if is_private_media:
        visible["privateMedia"] = True
        visible["mediaKey"] = media_path.name
    elif has_media_id:
        visible["privateMedia"] = True
        visible["previewCommand"] = normalize_command_name(name)
    return visible


def private_media_path(info: dict) -> Path | None:
    media_path = Path(str(info.get("media_path", "")))
    if not media_path:
        return None
    try:
        media_path.resolve().relative_to(UPLOADS_DIR.resolve())
    except (OSError, ValueError):
        return None
    return media_path if media_path.is_file() else None


def cached_preview_path(command_name: str, media_id: str, file_path: str = "") -> Path:
    suffix = Path(file_path).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(mimetypes.guess_type(file_path)[0] or "") or ".bin"
    digest = hashlib.sha256(f"{command_name}:{media_id}".encode()).hexdigest()
    return PREVIEW_CACHE_DIR / f"{digest}{suffix}"


def media_file_response(path: Path, command_type: str | None = None) -> web.FileResponse:
    content_type = COMMAND_MEDIA_TYPES.get(normalize_command_type(command_type))
    headers = {"Content-Type": content_type} if content_type else None
    return web.FileResponse(path, headers=headers)


async def download_telegram_preview(command_name: str, media_id: str, tokens: list[str]) -> Path | None:
    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    async with ClientSession() as session:
        for token in tokens:
            try:
                async with session.post(
                    f"https://api.telegram.org/bot{token}/getFile",
                    json={"file_id": media_id},
                    timeout=10,
                ) as response:
                    data = await response.json()
            except Exception:
                continue
            if not data.get("ok"):
                continue
            file_path = data.get("result", {}).get("file_path", "")
            if not file_path:
                continue
            destination = cached_preview_path(command_name, media_id, file_path)
            if destination.is_file():
                return destination
            try:
                async with session.get(
                    f"https://api.telegram.org/file/bot{token}/{file_path}",
                    timeout=30,
                ) as response:
                    if response.status != 200:
                        continue
                    with open(destination, "wb") as output:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            output.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                continue
            return destination if destination.is_file() else None
    return None


def validate_command_payload(data: dict, *, creating: bool) -> tuple[bool, str]:
    name = normalize_command_name(data.get("name", ""))
    command_type = normalize_command_type(data.get("type"))
    description = str(data.get("description", "")).strip()
    category = str(data.get("category", "")).strip()
    content = str(data.get("content", "")).strip()

    if not COMMAND_NAME_RE.match(name):
        return False, "Nome invalido. Use letras, numeros e underline, ate 32 caracteres."
    if category and len(category) > 40:
        return False, "Categoria muito longa. Use ate 40 caracteres."
    if creating and not description:
        return False, "Descricao obrigatoria."
    if creating and command_type == "texto" and not content:
        return False, "Conteudo obrigatorio para comandos de texto."
    return True, ""


def detect_magic_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
        return "mp3"
    if header.startswith(b"OggS"):
        return "ogg"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    return None


def validate_upload_signature(command_type: str, upload: FileField, header: bytes) -> str:
    declared = str(getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    allowed_declared = UPLOAD_CONTENT_TYPES.get(command_type, set())
    if allowed_declared and declared and declared not in allowed_declared:
        raise web.HTTPUnsupportedMediaType(text="Tipo MIME nao permitido para este comando.")

    magic_type = detect_magic_type(header)
    if magic_type not in UPLOAD_MAGIC_TYPES.get(command_type, set()):
        raise web.HTTPUnsupportedMediaType(text="Arquivo nao corresponde ao tipo escolhido.")
    return UPLOAD_MAGIC_EXTENSIONS[magic_type]


def save_uploaded_file(command_type: str, upload: FileField) -> str:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    header = upload.file.read(512)
    extension = validate_upload_signature(command_type, upload, header)
    destination = UPLOADS_DIR / f"{uuid.uuid4().hex}{extension}"
    total = len(header)
    with open(destination, "wb") as output:
        output.write(header)
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise web.HTTPRequestEntityTooLarge(
                    max_size=MAX_UPLOAD_BYTES,
                    actual_size=total,
                    text="Arquivo maior que 50 MB.",
                )
            output.write(chunk)
    return str(destination)


def apply_category(categories: list[str], category: str) -> list[str]:
    category = category or default_custom_category()
    if not category_exists(categories, category):
        categories.append(category)
        save_categories(categories)
    return categories


def build_command_record(data: dict, user: dict, *, upload_path: str | None, current: dict | None = None) -> dict:
    current = current.copy() if current else {}
    command_type = normalize_command_type(data.get("type") or current.get("tipo"))
    content = str(data.get("content", "")).strip()
    preview_url = str(data.get("previewUrl", "")).strip()

    record = {
        **current,
        "tipo": command_type,
        "descricao": str(data.get("description", "")).strip() or current.get("descricao", ""),
        "categoria": str(data.get("category", "")).strip() or current.get("categoria") or default_custom_category(),
        "conteudo": content if content or command_type == "texto" else current.get("conteudo", ""),
        "media_id": current.get("media_id"),
        "criado_por": current.get("criado_por") or user.get("id", 0),
        "origem": current.get("origem") or "mini_app_server",
    }
    if preview_url:
        record["previewUrl"] = preview_url
    elif "previewUrl" in current:
        record["previewUrl"] = current["previewUrl"]
    if upload_path:
        record["media_path"] = upload_path
        record["media_id"] = None
    elif current.get("media_path"):
        record["media_path"] = current["media_path"]
    now = datetime.now().isoformat(timespec="seconds")
    if current:
        record["data_alteracao"] = now
        record["origem_alteracao"] = "mini_app_server"
    else:
        record["data_criacao"] = now
    return record


def validate_init_data(init_data: str, token: str, max_age: int = DEFAULT_AUTH_MAX_AGE) -> dict | None:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age > 0 and time.time() - auth_date > max_age:
        return None

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None
    return user


async def validate_user_membership(user_id: int, tokens: list[str], chat_ids: list[int]) -> bool:
    if not chat_ids:
        return False

    async with ClientSession() as session:
        for token in tokens:
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/getChatMember"
                try:
                    async with session.post(
                        url,
                        json={"chat_id": chat_id, "user_id": user_id},
                        timeout=10,
                    ) as response:
                        data = await response.json()
                except Exception:
                    continue
                if not data.get("ok"):
                    continue
                status = data.get("result", {}).get("status")
                if status not in {"left", "kicked", None}:
                    return True
    return False


async def authorize_request(request: web.Request) -> dict | None:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    tokens = bot_tokens()
    if not init_data or not tokens:
        return None

    user = None
    for token in tokens:
        user = validate_init_data(init_data, token, max_age=auth_max_age())
        if user:
            break
    if not user:
        return None

    if not await validate_user_membership(int(user["id"]), tokens, authorized_chat_ids()):
        return None
    return user


def client_ip(request: web.Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return str(peer[0]) if peer else "unknown"


def is_rate_limited(request: web.Request, user_id: int) -> bool:
    limit, window = rate_limit_config()
    now = time.monotonic()
    route = request.match_info.route.resource.canonical if request.match_info.route.resource else request.path
    key = (client_ip(request), route, int(user_id))
    bucket = _rate_limit_buckets[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


async def index(_request: web.Request) -> web.FileResponse:
    response = web.FileResponse(MINI_APP_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


async def catalog(request: web.Request) -> web.Response:
    user = request.get("authorized_user") or await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)
    return web.json_response(load_catalog_payload())


async def admin_action(request: web.Request) -> web.Response:
    user = request.get("authorized_user") or await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    action = str(payload.get("action", ""))
    commands = load_json(COMANDOS_FILE, {})
    categories = load_json(CUSTOM_CATEGORIES_FILE, [])

    if action == "delete_command":
        name = normalize_command_name(payload.get("name", ""))
        key = next((item for item in commands if item.lower() == name), None)
        if not key:
            return web.json_response({"error": "not_found"}, status=404)
        old_path = Path(str(commands[key].get("media_path", "")))
        del commands[key]
        save_json(COMANDOS_FILE, commands)
        if old_path.is_file() and old_path.is_relative_to(UPLOADS_DIR):
            old_path.unlink(missing_ok=True)
        return web.json_response({"ok": True, "catalog": load_catalog_payload()})

    if action in {"create_category", "update_category", "delete_category"}:
        name = str(payload.get("name", "")).strip()
        new_name = str(payload.get("newName", "")).strip()
        if not name or len(name) > 40 or len(new_name) > 40:
            return web.json_response({"error": "invalid_category"}, status=400)
        current = category_exists(categories, name)
        if action == "create_category":
            if not current:
                categories.append(name)
                save_categories(categories)
            return web.json_response({"ok": True, "catalog": load_catalog_payload()})
        if action == "update_category":
            if not new_name:
                return web.json_response({"error": "missing_new_name"}, status=400)
            if not current:
                return web.json_response({"error": "not_found"}, status=404)
            categories = [new_name if item.lower() == name.lower() else item for item in categories]
            for info in commands.values():
                if str(info.get("categoria") or info.get("category") or "").lower() == name.lower():
                    info["categoria"] = new_name
            save_categories(categories)
            save_json(COMANDOS_FILE, commands)
            return web.json_response({"ok": True, "catalog": load_catalog_payload()})
        if current:
            categories = [item for item in categories if item.lower() != name.lower()]
        for info in commands.values():
            if str(info.get("categoria") or info.get("category") or "").lower() == name.lower():
                info["categoria"] = default_custom_category()
        save_categories(categories)
        save_json(COMANDOS_FILE, commands)
        return web.json_response({"ok": True, "catalog": load_catalog_payload()})

    if action == "backlog_add":
        text = str(payload.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty_text"}, status=400)
        backlog = load_json(BACKLOG_FILE, [])
        next_id = max((item.get("id", 0) for item in backlog if isinstance(item, dict)), default=0) + 1
        backlog.append({
            "id": next_id,
            "sugestao": text,
            "autor": user.get("first_name") or user.get("username") or "Mini App",
            "autor_id": user.get("id", 0),
            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "origem": "mini_app_server",
        })
        save_json(BACKLOG_FILE, backlog)
        return web.json_response({"ok": True, "id": next_id})

    return web.json_response({"error": "unknown_action"}, status=400)


async def upload_command(request: web.Request) -> web.Response:
    user = request.get("authorized_user") or await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)

    form = await request.post()
    data = {key: str(value) for key, value in form.items() if not isinstance(value, FileField)}
    mode = str(data.get("mode", "create"))
    creating = mode != "update"
    ok, error = validate_command_payload(data, creating=creating)
    if not ok:
        return web.json_response({"error": error}, status=400)

    name = normalize_command_name(data.get("name", ""))
    upload = form.get("media")
    has_upload = isinstance(upload, FileField) and bool(upload.filename)

    commands = load_json(COMANDOS_FILE, {})
    categories = load_json(CUSTOM_CATEGORIES_FILE, [])
    key = next((item for item in commands if item.lower() == name), None)
    if creating and key:
        return web.json_response({"error": "command_exists"}, status=409)
    if not creating and not key:
        return web.json_response({"error": "not_found"}, status=404)

    current = commands.get(key) if key else None
    command_type = normalize_command_type(data.get("type") or (current or {}).get("tipo"))
    if creating and command_type != "texto" and not has_upload:
        return web.json_response({"error": "Arquivo obrigatorio para comandos de midia."}, status=400)
    if command_type == "texto":
        upload = None
        has_upload = False
    data["type"] = command_type

    upload_path = save_uploaded_file(command_type, upload) if has_upload else None
    record = build_command_record(data, user, upload_path=upload_path, current=current)
    if upload_path and key:
        previous_path = Path(str(commands[key].get("media_path", "")))
        if previous_path.is_file() and previous_path.is_relative_to(UPLOADS_DIR):
            previous_path.unlink(missing_ok=True)
    commands[key or name] = record
    apply_category(categories, record["categoria"])
    save_json(COMANDOS_FILE, commands)
    return web.json_response({"ok": True, "command": name, "catalog": load_catalog_payload()})


async def private_media(request: web.Request) -> web.StreamResponse:
    user = request.get("authorized_user") or await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)
    key = request.match_info.get("key", "")
    if not MEDIA_KEY_RE.match(key):
        return web.json_response({"error": "not_found"}, status=404)
    media_path = UPLOADS_DIR / key
    if not media_path.is_file():
        return web.json_response({"error": "not_found"}, status=404)
    return media_file_response(media_path)


async def command_preview(request: web.Request) -> web.StreamResponse:
    user = request.get("authorized_user") or await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)
    name = normalize_command_name(request.match_info.get("name", ""))
    if not COMMAND_NAME_RE.match(name):
        return web.json_response({"error": "not_found"}, status=404)

    commands = load_json(COMANDOS_FILE, {})
    key = next((item for item in commands if item.lower() == name), None)
    if not key:
        return web.json_response({"error": "not_found"}, status=404)
    info = commands.get(key) or {}

    media_path = private_media_path(info)
    if media_path:
        return media_file_response(media_path, info.get("tipo"))

    media_id = str(info.get("media_id") or "").strip()
    if not media_id:
        return web.json_response({"error": "not_found"}, status=404)
    downloaded = await download_telegram_preview(name, media_id, bot_tokens())
    if not downloaded:
        return web.json_response({"error": "preview_unavailable"}, status=404)
    return media_file_response(downloaded, info.get("tipo"))


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 1024 * 1024)

    @web.middleware
    async def api_auth_rate_limit_middleware(request, handler):
        if request.path.startswith("/api/"):
            user = await authorize_request(request)
            if not user:
                return web.json_response({"error": "unauthorized"}, status=403)
            if is_rate_limited(request, int(user["id"])):
                return web.json_response({"error": "rate_limited"}, status=429)
            request["authorized_user"] = user
        return await handler(request)

    @web.middleware
    async def no_cache_middleware(request, handler):
        response = await handler(request)
        if request.path in {"/", "/index.html", "/app.js", "/styles.css", "/catalog.json"}:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers.update(SECURITY_HEADERS)
        return response

    app.middlewares.append(api_auth_rate_limit_middleware)
    app.middlewares.append(no_cache_middleware)
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/catalog.json", catalog)
    app.router.add_post("/api/admin/action", admin_action)
    app.router.add_post("/api/admin/upload-command", upload_command)
    app.router.add_get("/api/media/{key}", private_media)
    app.router.add_get("/api/preview/{name}", command_preview)
    app.router.add_static("/", MINI_APP_DIR, show_index=False)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor privado do Mini App Telegram.")
    parser.add_argument("--host", default=os.getenv("MINI_APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MINI_APP_PORT", "8080")))
    parser.add_argument("--env-file", default=os.getenv("ENV_FILE"))
    args = parser.parse_args()

    load_environment(args.env_file)
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
