import argparse
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import ClientSession, web

from packages.config import load_environment, parse_chat_ids


ROOT = Path(__file__).resolve().parents[2]
MINI_APP_DIR = ROOT / "apps" / "mini_app"
DEFAULT_AUTH_MAX_AGE = 24 * 60 * 60


def bot_tokens() -> list[str]:
    tokens = []
    for name in ("BOT_TOKEN", "BOT_TOKEN_COMANDOS"):
        token = os.getenv(name, "").strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def authorized_chat_ids() -> list[int]:
    return parse_chat_ids(os.getenv("GRUPOS_AUTORIZADOS", ""))


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
        user = validate_init_data(init_data, token)
        if user:
            break
    if not user:
        return None

    if not await validate_user_membership(int(user["id"]), tokens, authorized_chat_ids()):
        return None
    return user


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(MINI_APP_DIR / "index.html")


async def catalog(request: web.Request) -> web.Response:
    user = await authorize_request(request)
    if not user:
        return web.json_response({"error": "unauthorized"}, status=403)
    return web.FileResponse(MINI_APP_DIR / "catalog.json")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/catalog.json", catalog)
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
