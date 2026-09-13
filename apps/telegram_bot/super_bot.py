# Teste do sistema de atualização
import sys
import os
import re
import json
import time
import random
import asyncio
import logging
import uuid
import threading
import shutil
import psutil
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse
from collections import defaultdict
import aiohttp

from pyrogram import Client, filters, idle, raw
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
try:
    from pyrogram.file_id import FileId, FileType
except ImportError:
    FileId = None
    FileType = None
from pathlib import Path

def flex_command(commands, prefixes="/", case_sensitive=False):
    if isinstance(commands, str):
        commands = [commands]
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    
    commands = [c if case_sensitive else c.lower() for c in commands]
    
    async def func(flt, client, message):
        text = message.text or message.caption
        message.command = None
        if not text:
            return False
            
        words = text.split()
        for i, word in enumerate(words):
            for prefix in prefixes:
                if word.startswith(prefix):
                    cmd_name = word[len(prefix):].split('@')[0]
                    if not case_sensitive:
                        cmd_name = cmd_name.lower()
                    if cmd_name in flt.commands:
                        message.command = [cmd_name] + words[i+1:]
                        return True
        return False
        
    return filters.create(func, commands=commands)

filters.command = flex_command

# Fix the import and RAÍZ problem:
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)

from packages.database import database_manager as db
from packages.command_catalog import SUPER_COMMANDS, command_names
from packages.config import (
    DOWNLOADS_DIR,
    LOG_DIR,
    ensure_runtime_dirs,
    get_bool_env,
    get_int_env,
    instagram_cookie_path,
    instagram_secondary_cookie_path,
    load_environment,
    mini_app_url,
    parse_chat_ids,
)
from packages.logging_config import configure_rotating_logging
from packages.private_access import guard_authorized_group_chat, guard_private_chat_access
from packages.telegram_ui import (
    build_bot_commands,
    reply_command_menu,
    set_bot_commands_via_bot_api,
    set_bot_commands_menu_button_via_bot_api,
)
from apps.telegram_bot.facebook import ACCESS_NOTICE, UNAVAILABLE_NOTICE
from apps.telegram_bot.downloaders import (
    DownloadCancelled,
    ACTIVE_DIRECTORIES,
    managed_downloads,
)
from apps.telegram_bot.duplicates import normalizar_link_social
from apps.telegram_bot.instagram import (
    fetch_instagram_profile,
    get_profile_username,
    detect_profile_privado as _detect_profile_privado,
    get_cookie_failure_reason,
    _auto_login_and_save_cookies,
    inspect_cookie_health,
    validate_cookie_health,
    reset_cookies_bad,
)
from apps.telegram_bot.instagram_profile_card import gerar_card as _gerar_card_perfil
from apps.telegram_bot.media_utils import progresso_upload as _progresso_upload
from apps.telegram_bot.text_utils import dividir_texto_longo
from apps.telegram_bot.twitter import match_tweet_url, match_profile_url, build_profile_url, build_follow_info_url
from apps.telegram_bot.errors import (
    AuthenticationRequired,
    MediaTooLarge,
    MediaTooLong,
    SocialMediaError,
)
from apps.telegram_bot.handlers.social import SocialMediaPipeline, SocialPipelineConfig
from apps.telegram_bot.handlers.callbacks import ExpiringRegistry
from apps.telegram_bot.handlers.commands import build_admin_only
from apps.telegram_bot.handlers.links import InFlightLinks, extract_supported_url
from apps.telegram_bot.handlers.moderation import SlidingWindowLimiter
from apps.telegram_bot.services.download_manager import detect_platform

load_environment()
ensure_runtime_dirs()
db.init_db()

# -----------------------------------------
# CONSTANTES E ESTADO GLOBAL
# -----------------------------------------
START_TIME = time.time()
DOWNLOAD_COUNT = 0
DOWNLOAD_COUNT_LOCK = asyncio.Lock()

LIMITE_DURACAO = 600
LIMITE_TAMANHO = max(1, get_int_env("MAX_MEDIA_BYTES", 5_000_000_000))
MAX_DOWNLOADS = max(1, get_int_env("MAX_DOWNLOADS", 3))
RATE_LIMIT = 10
RATE_JANELA = 60
IG_MEDIA_DOWNLOAD_CONCURRENCY = max(1, get_int_env("IG_MEDIA_DOWNLOAD_CONCURRENCY", 3))
PROFILE_PICTURE_MAX_BYTES = max(1, get_int_env("PROFILE_PICTURE_MAX_BYTES", 10 * 1024 * 1024))
PROCESSING_URL_TTL = max(60, get_int_env("PROCESSING_URL_TTL", 2 * 60 * 60))

AUDIO_BOCA_LEITE_DIR = os.path.join(RAIZ, "assets", "audios")
PASTA_DOWNLOADS = DOWNLOADS_DIR
COOKIE_PATH = str(instagram_cookie_path())
SECONDARY_COOKIE_PATH = str(instagram_secondary_cookie_path())
MINI_APP_URL = mini_app_url()

API_ID = get_int_env("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODO_ZUEIRA = get_bool_env("MODO_ZUEIRA", True)
ADMIN_ID = get_int_env("ADMIN_ID", 0)

_grupos_raw = os.getenv("GRUPOS_AUTORIZADOS", "")
GRUPOS_AUTORIZADOS = parse_chat_ids(_grupos_raw)

DOMINIOS_PERMITIDOS = [
    "x.com", "twitter.com", "youtube.com", "youtu.be",
    "instagram.com", "instagr.am", "tiktok.com", "threads.net",
    "pinterest.com", "pin.it", "facebook.com", "fb.com", "fb.watch"
]

# -----------------------------------------
# POOL DE SESSÕES HTTP REUTILIZÁVEIS
# Reutilizar ClientSession evita o custo de handshake TLS/DNS
# a cada chamada (principalmente em picos de uso).
# -----------------------------------------
_http_session: aiohttp.ClientSession | None = None
_http_session_lock = asyncio.Lock()
async def get_http_session() -> aiohttp.ClientSession:
    """Retorna uma sessão aiohttp compartilhada, criada sob demanda."""
    global _http_session
    if _http_session is None or _http_session.closed:
        async with _http_session_lock:
            if _http_session is None or _http_session.closed:
                _http_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10),
                    raise_for_status=False,
                )
    return _http_session

async def close_http_session() -> None:
    """Fecha a sessão compartilhada (chamado no shutdown)."""
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
    _http_session = None

# -----------------------------------------
from apps.telegram_bot.mensagens_erro import (
    ERROS_RATE_LIMIT,
    ERROS_INESPERADO,
    ERROS_INSTAGRAM,
    ERROS_X,
    ERROS_LINK_PROCESSANDO,
    ERROS_COOLDOWN,
    ERROS_RETRY_SEM_MSG,
    ERROS_RETRY_SEM_RESPOSTA,
    ERROS_BLOQ_CMD,
    ERROS_BLOQ_TENTATIVA
)

def erro_aleatorio(lista, **kwargs):
    """Escolhe uma mensagem de erro aleatória da lista, formatando com kwargs."""
    msg = random.choice(lista)
    if kwargs:
        msg = msg.format(**kwargs)
    return msg

PACKS = {"repetido": "POSTREPETIDO", "meus": "Meus325", "monkes": "Monkes"}

semaforo = asyncio.Semaphore(MAX_DOWNLOADS)
_fila_espera = 0
_fila_lock = asyncio.Lock()
_retry_cache = ExpiringRegistry(ttl=3600, max_entries=500)
_failed_url_cache = {}  # url_norm -> timestamp (cooldown para URLs que falharam recentemente)
_inflight_links = InFlightLinks(ttl=PROCESSING_URL_TTL)
_processing_urls = _inflight_links.entries
_processing_lock = _inflight_links.lock
_rate_limiter = SlidingWindowLimiter(limit=RATE_LIMIT, window_seconds=RATE_JANELA)
_usuarios_bloqueados = {}  # user_id -> timestamp (cooldown de castigo de 5min)
_uso_bloq = defaultdict(list)  # admin_id -> [timestamps dos blocks aplicados hoje]
_ultimo_link_por_usuario = {}  # user_id -> {"url_norm": str, "url_raw": str, "timestamp": float}
_bloqueios_por_link = defaultdict(set)  # user_id -> set de url_norms que já causaram bloqueio
_downloads_cancelaveis = {}  # msg_id -> (threading.Event, user_id)
_long_requests = {}

# -----------------------------------------
# LOGGING
# -----------------------------------------
configure_rotating_logging(LOG_DIR, "bot.log")

log = logging.getLogger("SuperBot")
admin_only = build_admin_only(lambda: ADMIN_ID, logger=log)

# -----------------------------------------
# CLIENTE
# -----------------------------------------
try:
    _runtime_loop = asyncio.get_event_loop()
except RuntimeError:
    # Python 3.11+ nao cria mais um loop implicitamente em todo contexto.
    _runtime_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_runtime_loop)
app = Client("meu_super_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


@app.on_message(filters.private, group=-1)
async def bloquear_privado_nao_autorizado(client, message):
    await guard_private_chat_access(
        client,
        message,
        GRUPOS_AUTORIZADOS,
        bot_label="Super Bot",
        bot_token=BOT_TOKEN,
        mini_app_url=MINI_APP_URL,
        bot_commands=SUPER_COMMANDS,
    )


@app.on_message(filters.group, group=-1)
async def sair_de_grupo_nao_autorizado(client, message):
    await guard_authorized_group_chat(client, message, GRUPOS_AUTORIZADOS, bot_label="Super Bot")

# -----------------------------------------
# STICKERS
# -----------------------------------------
async def metralhadora_stickers(client, chat_id):
    try:
        async def get_stickers(pack_short_name, quantity):
            sticker_set = await client.invoke(
                raw.functions.messages.GetStickerSet(
                    stickerset=raw.types.InputStickerSetShortName(short_name=pack_short_name),
                    hash=0
                )
            )
            selecionados = random.sample(sticker_set.documents, min(len(sticker_set.documents), quantity))
            ids = []
            for doc in selecionados:
                if FileId and FileType:
                    # Cria instância do FileId e depois codifica
                    fid_obj = FileId(
                        file_type=FileType.STICKER,
                        dc_id=doc.dc_id,
                        media_id=doc.id,
                        access_hash=doc.access_hash,
                        file_reference=doc.file_reference
                    )
                    fid = fid_obj.encode()
                    ids.append(fid)
                else:
                    # Fallback: try to get file_id from doc attributes
                    ids.append(str(doc.id))
            return ids

        final_ids = []
        final_ids.extend(await get_stickers(PACKS["repetido"], 3))
        final_ids.extend(await get_stickers(PACKS["meus"], 1))
        final_ids.extend(await get_stickers(PACKS["monkes"], 1))

        for sticker_id in final_ids:
            await client.send_sticker(chat_id, sticker_id)
            await asyncio.sleep(0.4)
    except Exception as e:
        log.error(f"Erro stickers: {e}")

# -----------------------------------------
# UTILITÁRIOS
# -----------------------------------------
def verificar_rate_limit(user_id: int) -> bool:
    return _rate_limiter.allow(user_id)

def chat_autorizado(chat_id: int) -> bool:
    if not GRUPOS_AUTORIZADOS:
        return True
    return chat_id in GRUPOS_AUTORIZADOS

# -----------------------------------------
# MOTOR DE DOWNLOAD
# -----------------------------------------
async def avisar_video_longo(msg_espera, url, usuario, message):
    token = uuid.uuid4().hex
    _long_requests[token] = (url, usuario, message, time.monotonic())
    texto_aviso = '⏱️ Vídeo com mais de 10 minutos. Deseja continuar o download?'

    botoes = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Baixar", callback_data=f"forcelong_{token}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"cancellong_{token}")]
    ])

    await msg_espera.edit_text(texto_aviso, reply_markup=botoes)
    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)


def _caminho_temporario(prefixo: str, message, indice: int, extensao: str) -> Path:
    """Gera nomes unicos mesmo quando chats diferentes compartilham message.id."""
    chat_id = getattr(getattr(message, "chat", None), "id", "chat")
    ext = extensao.lower().lstrip(".") or "bin"
    return PASTA_DOWNLOADS / f"{prefixo}_{chat_id}_{message.id}_{indice}_{uuid.uuid4().hex}.{ext}"


def _formatar_numero_perfil(valor):
    if valor is None:
        return "N/A"
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        return str(valor)
    if valor >= 1_000_000:
        return f"{valor / 1_000_000:.1f}M".replace(".0M", "M")
    if valor >= 1_000:
        return f"{valor / 1_000:.1f}k".replace(".0k", "k")
    return str(valor)


def montar_resposta_perfil_instagram(profile):
    nome = profile.get("full_name") or profile.get("username") or "Perfil"
    username = profile.get("username") or ""
    verificado = " • verificado" if profile.get("is_verified") else ""
    if profile.get("is_private") is None:
        privacidade = "Nao informado"
    else:
        privacidade = "Privado" if profile.get("is_private") else "Publico"
    bio = profile.get("biography")
    bio = "Bio indisponível." if bio is None else (bio.strip() or "Sem bio.")

    linhas = [
        f"**Instagram: {nome}**",
        f"@{username}{verificado}",
        "",
        bio,
        "",
        f"Posts: {_formatar_numero_perfil(profile.get('posts'))}",
        f"Seguidores: {_formatar_numero_perfil(profile.get('followers'))}",
        f"Seguindo: {_formatar_numero_perfil(profile.get('following'))}",
        f"Perfil: {privacidade}",
    ]
    if profile.get("external_url"):
        linhas.append(f"Link: {profile['external_url']}")
    return "\n".join(linhas)[:1024]


async def _baixar_bytes_limitados(
    session,
    url: str,
    limite_bytes: int,
    *,
    headers: dict | None = None,
) -> bytes | None:
    """Baixa bytes pequenos com limite real mesmo quando o servidor omite Content-Length."""
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            return None
        if resp.content_length and resp.content_length > limite_bytes:
            return None
        dados = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            dados.extend(chunk)
            if len(dados) > limite_bytes:
                return None
        return bytes(dados)


async def responder_perfil_instagram(client, message, url):
    profile = await fetch_instagram_profile(
        url,
        COOKIE_PATH,
        secondary_cookie_path=SECONDARY_COOKIE_PATH,
    )
    if not profile:
        privado = await _detect_profile_privado(
            url,
            COOKIE_PATH,
            secondary_cookie_path=SECONDARY_COOKIE_PATH,
        )
        if privado:
            await message.reply_text(
                "🔒 **Perfil privado** — não consigo puxar as informações e a foto de um "
                "perfil privado do Instagram (pede login). Peça ao dono para tornar o "
                "perfil público ou compartilhe um post/story dele."
            )
        else:
            await message.reply_text("❌ Não consegui carregar os dados desse perfil do Instagram.")
        return

    is_privado = profile.get("is_private", False)

    # Baixa a foto do perfil (com limite de tamanho) para montar o card
    foto_bytes = None
    photo_url = ""
    photo_urls = dict.fromkeys([
        profile.get("profile_pic_url"), *(profile.get("profile_pic_urls") or []),
    ])
    for candidata in photo_urls:
        if not candidata:
            continue
        try:
            dl_session = await get_http_session()
            foto_bytes = await _baixar_bytes_limitados(
                dl_session,
                candidata,
                PROFILE_PICTURE_MAX_BYTES,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                                  'Chrome/125.0.0.0 Safari/537.36',
                    'Referer': 'https://www.instagram.com/',
                },
            )
            if foto_bytes:
                photo_url = candidata
                break
        except Exception as e:
            log.warning("Falha ao baixar foto do perfil Instagram: %s", str(e)[:150])

    # Monta o card estilizado (roda em thread para não travar o loop)
    try:
        card_bytes = await asyncio.to_thread(_gerar_card_perfil, profile, foto_bytes)
        card_path = _caminho_temporario("card_insta", message, 0, "png")
        with open(card_path, "wb") as f:
            f.write(card_bytes)
        try:
            await client.send_photo(
                message.chat.id,
                str(card_path),
                reply_to_message_id=message.id,
            )
            try:
                os.remove(card_path)
            except OSError:
                pass
            if is_privado:
                await message.reply_text("🔒 **Perfil privado** — as informações abaixo podem estar incompletas, pois visualizações de conteúdo exigem login.")
            return
        except Exception as e:
            log.warning("Falha ao enviar card do Instagram: %s", str(e)[:150])
            card_path.unlink(missing_ok=True)
    except Exception as e:
        log.warning("Falha ao gerar card do Instagram: %s", str(e)[:150])

    # Fallback: envia a foto do perfil pura
    if foto_bytes:
        try:
            await client.send_photo(message.chat.id, photo_url, caption=montar_resposta_perfil_instagram(profile), reply_to_message_id=message.id)
            return
        except Exception as e:
            log.warning("Fallback foto do perfil Instagram: %s", str(e)[:150])

    caption = montar_resposta_perfil_instagram(profile)
    if is_privado:
        caption = "🔒 **Perfil privado** — as informações podem estar incompletas.\n\n" + caption
    await message.reply_text(caption)


async def detect_x_private(username: str) -> dict | None:
    """Busca dados de um perfil do X/Twitter e informa se é protegido (privado).

    Usa a API do vxtwitter para perfis (sem /status), que retorna `protected`.
    Retorna um dict com os dados do perfil, ou None se não der pra determinar
    (conta inexistente/deletada/erro de rede).
    """
    # 1) vxtwitter (retorna protected + dados ricos)
    try:
        session = await get_http_session()
        async with session.get(build_profile_url(username), timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                dados = await resp.json()
                if isinstance(dados, dict) and "screen_name" in dados:
                    profile = {
                        "username": dados.get("screen_name") or username,
                        "name": dados.get("name") or "",
                        "description": dados.get("description") or "",
                        "protected": bool(dados.get("protected")),
                        "followers": dados.get("followers_count"),
                        "following": dados.get("following_count"),
                        "posts": dados.get("tweet_count"),
                        "location": dados.get("location") or "",
                        "verified": bool(dados.get("verified")),
                        "id": dados.get("id"),
                        "profile_image_url": dados.get("profile_image_url") or "",
                        "created_at": dados.get("created_at") or "",
                    }
                    return profile
            elif resp.status in (404, 400):
                log.info("vxtwitter perfil @%s → %d (conta inexistente?)", username, resp.status)
                return None
    except Exception as e:
        log.info("⚠️ vxtwitter perfil @%s falhou: %s", username, str(e)[:120])

    # 2) Fallback: endpoint público de follow-button
    try:
        session = await get_http_session()
        async with session.get(build_follow_info_url(username), timeout=aiohttp.ClientTimeout(total=15)) as r2:
            if r2.status == 200:
                dados = await r2.json(content_type=None)
                if isinstance(dados, list) and dados:
                    first = dados[0]
                    if isinstance(first, dict) and "protected" in first:
                        return {
                            "username": first.get("screen_name") or username,
                            "name": first.get("name") or "",
                            "description": "",
                            "protected": bool(first.get("protected")),
                            "followers": None, "following": None, "posts": None,
                            "location": "", "verified": bool(first.get("verified")),
                            "id": first.get("id"), "profile_image_url": "", "created_at": "",
                        }
                elif isinstance(dados, dict) and "protected" in dados:
                    return {
                        "username": username, "name": "", "description": "",
                        "protected": bool(dados.get("protected")),
                        "followers": None, "following": None, "posts": None,
                        "location": "", "verified": False, "id": None,
                        "profile_image_url": "", "created_at": "",
                    }
    except Exception as e:
        log.info("⚠️ Follow-button @%s falhou: %s", username, str(e)[:120])

    return None


def _extrair_ano_criacao(created_at: str) -> int | None:
    """Extrai o ano de criação da conta de uma data do Twitter (ex: 2007)."""
    if not created_at:
        return None
    match = re.search(r'\b(19|20)\d{2}\b', created_at)
    return int(match.group(0)) if match else None


def montar_resposta_perfil_x(profile: dict) -> str:
    """Monta o texto-resumo de um perfil público do X."""
    nome = profile.get("name") or profile.get("username") or "Perfil"
    username = profile.get("username") or ""
    verificado = " ✔️ Verificado" if profile.get("verified") else ""
    bio = profile.get("description") or "Sem bio."
    linhas = [
        f"**X/Twitter: {nome}**",
        f"@{username}{verificado}",
        "",
        bio,
        "",
        f"📊 **{_formatar_numero_perfil(profile.get('posts'))}** posts",
        f"👥 **{_formatar_numero_perfil(profile.get('followers'))}** seguidores",
        f"↗️ **{_formatar_numero_perfil(profile.get('following'))}** seguindo",
    ]

    # Razão seguidores/seguindo como dica de engajamento
    seguindo = profile.get("following")
    seguidores = profile.get("followers")
    if isinstance(seguindo, int) and isinstance(seguidores, int) and seguindo > 0:
        razao = seguidores / seguindo
        if 2 <= razao <= 500:
            linhas.append(f"📈 Forte engajamento (≈{int(razao)}× seguidores/seguindo)")

    ano = _extrair_ano_criacao(profile.get("created_at"))
    if ano:
        corrente = datetime.now().year
        idade = max(1, corrente - ano)
        linhas.append(f"🕰️ No X desde {ano} ({idade} {'ano' if idade == 1 else 'anos'})")
    if profile.get("location"):
        linhas.append(f"📍 {profile['location']}")
    return "\n".join(linhas)[:1024]


async def responder_perfil_x(client, message, url):
    """Responde a um link de perfil do X/Twitter: detecta privado ou monta resumo."""
    match = match_profile_url(url)
    username = match.group(1) if match else None
    if not username:
        await message.reply_text("❌ Não consegui identificar o perfil do X.")
        return

    status_msg = await message.reply_text(f"🔍 Verificando perfil @{username}...")
    profile = await detect_x_private(username)

    if not profile:
        await status_msg.edit_text(
            f"❌ Não consegui verificar o perfil @{username} do X.\n"
            "Pode ser conta inexistente, deletada, suspensa, ou um bloqueio da API."
        )
        return

    if profile.get("protected"):
        await status_msg.edit_text(
            f"🔒 **@{username}** é um perfil **privado** (conta protegida) no X.\n\n"
            "**O que isso significa?**\n"
            f"• As postagens de @{username} **só aparecem para quem ele segue**.\n"
            "• Todo o conteúdo (tweets, fotos, vídeos e reels) fica **escondido** do público.\n"
            "• Não existe API pública para puxar esse conteúdo — o X bloqueia devidamente.\n\n"
            "**Como resolver?**\n"
            "1. Peça pra ele criar um **link de um post** (x.com/<user>/status/<id>) — mesmo privado, "
            "quando você está logado no perfil que o segue, dá pra baixar.\n"
            "2. Ou peça pra ele **tornar o perfil público** temporariamente.\n\n"
            "Enquanto isso, não consigo trazer tweets, fotos nem vídeos desse perfil. 🔒"
        )
        return

    caption = montar_resposta_perfil_x(profile)
    foto = profile.get("profile_image_url") or ""
    if foto:
        try:
            await client.send_photo(
                message.chat.id, foto, caption=caption,
                reply_to_message_id=message.id,
            )
            await status_msg.delete()
            return
        except Exception as e:
            log.warning("Falha ao enviar foto do perfil X: %s", str(e)[:150])
    await status_msg.edit_text(caption)


# -----------------------------------------
# COMANDOS DE RANKING (SQLite)
# -----------------------------------------
@app.on_message(filters.command("ranking"))
async def cmd_ranking(client, message):
    if not chat_autorizado(message.chat.id):
        return
    res = db.get_ranking_semanal()
    if not res:
        return await message.reply_text("🏆 Grupo limpo na última semana!")
    txt = "**📊 Ranking Semanal**\n\n"
    for i, (nome, total) in enumerate(res, 1):
        txt += f"{i}º {nome}: {total} vacilos\n"
    await message.reply_text(txt)

@app.on_message(filters.command("bocadeleite"))
async def cmd_mensal(client, message):
    if not chat_autorizado(message.chat.id):
        return
    v_antigo, m_antigo = db.fechar_mes_passado_se_preciso()
    if v_antigo:
        await message.reply_text(f"**📅 Mês Fechado:** O campeão de {m_antigo} foi **{v_antigo}**! 🏆")
    ranking = db.get_lider_mes_atual()
    if not ranking:
        return await message.reply_text("✨ Mês limpo!")
    txt = f"**🏆 Líderes de {datetime.now().strftime('%B').upper()}**\n\n"
    for i, (nome, total) in enumerate(ranking[:3], 1):
        med = "1" if i==1 else "2" if i==2 else "3"
        txt += f"{med} {nome}: {total} vacilos\n"
    await message.reply_text(txt)

@app.on_message(filters.command("anual"))
async def cmd_anual(client, message):
    if not chat_autorizado(message.chat.id):
        return
    hall = db.get_hall_da_fama_ano()
    if not hall:
        return await message.reply_text("🏆 Sem campeões registrados ainda.")
    txt = f"**👑 Boca de Leite do Ano ({datetime.now().year})**\n\n"
    for i, (nome, vits) in enumerate(hall, 1):
        txt += f"{i}º {nome}: {vits} meses ganhos\n"
    await message.reply_text(txt)

@app.on_message(filters.command(["help", "menu"]))
async def cmd_help(client, message):
    if not chat_autorizado(message.chat.id):
        return
    await reply_command_menu(
        message,
        "🤖 Guia do Super Bot",
        SUPER_COMMANDS,
        MINI_APP_URL,
        log,
        bot_token=BOT_TOKEN,
        ephemeral=True,
        public_fallback=False,
    )

@app.on_message(filters.command("repetido"))
async def cmd_repetido_manual(client, message):
    if not chat_autorizado(message.chat.id):
        return
    if not message.reply_to_message:
        return await message.reply_text("💡 Dica: Use este comando em resposta a alguém que postou repetido!")

    target = message.reply_to_message
    if target.from_user and target.from_user.is_bot:
        return await message.reply_text("🚨 BURRO DO CARALHO! Tá marcando o BOT como repetido? Vai tomar no cu, imbecil. O repetido é pra marcar USUÁRIO, não bot seu animal de teta.")

    mencao = target.from_user.mention
    target_name = target.from_user.first_name or "Membro"
    target_username = f"(@{target.from_user.username})" if target.from_user.username else ""
    target_full = f"{target_name} {target_username}".strip()
    txt = f"**🚨 BOCA DE LEITE {mencao}! (Castigo Manual)**"

    # Registra vacilo no ranking para a pessoa pega
    db.registrar_vacilo_manual(target.from_user.id, target_full)

    lista_audios = ["boca-de-leite.ogg", "aids.ogg", "de-novo-cac.ogg"]
    for i, nome_audio in enumerate(lista_audios):
        caminho = Path(AUDIO_BOCA_LEITE_DIR) / nome_audio
        if caminho.exists():
            leg = txt if i == 0 else None
            await client.send_voice(message.chat.id, str(caminho), caption=leg, reply_to_message_id=target.id)
            await asyncio.sleep(0.7)
    await metralhadora_stickers(client, message.chat.id)

@app.on_message(filters.command("comi"))
async def cmd_comi(client, message):
    if not chat_autorizado(message.chat.id):
        return
    if not MODO_ZUEIRA:
        return
    try:
        membros = []
        async for m in client.get_chat_members(message.chat.id, limit=200):
            if not m.user.is_bot and m.user.id != message.from_user.id:
                membros.append(m.user)

        if not membros:
            return await message.reply_text("🤷 Ué, não tem ninguém aqui além de mim e você...")

        random.shuffle(membros)
        vitima = random.choice(membros)
        frases = [
            f"🍽️ Hmm... Hoje eu comi o(a) {vitima.mention}! Estava uma delícia.",
            f"🔥 Nossa, acabei de jantar o(a) {vitima.mention}. Recomendado!",
            f"😈 {vitima.mention} foi devorado(a) com sucesso!"
        ]
        await message.reply_text(random.choice(frases))
    except Exception as e:
        log.error(f"Erro no /comi: {e}")

@app.on_message(filters.command("bloq"))
async def cmd_bloq(client, message):
    if not chat_autorizado(message.chat.id):
        return
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("Uso: /bloq @usuario ou responda a alguém.")
    
    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    else:
        try:
            target_user = await client.get_users(message.command[1])
        except Exception:
            pass

    if not target_user:
        return await message.reply_text("Não consegui identificar o usuário. Mencione ou responda.")
    
    if getattr(target_user, "is_bot", False):
        return await message.reply_text("Vai se foder, não vou bloquear um bot.")
        
    agora = time.time()
    target_id = target_user.id
    
    # --- Verifica se há motivo válido para o bloqueio ---
    link_info = _ultimo_link_por_usuario.get(target_id)
    link_motivo = None

    # Caso 1: usuário enviou um link nos últimos 10 minutos
    if link_info and (agora - link_info["timestamp"] < 600):
        link_motivo = link_info["url_norm"]

    # Caso 2: sem link recente, verifica se o comando menciona um link
    if not link_motivo:
        url_no_cmd = re.search(r'((?:https?://|www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)', message.text or "")
        if url_no_cmd and link_info:
            url_raw_cmd = url_no_cmd.group(1)
            if not url_raw_cmd.startswith('http'):
                url_raw_cmd = 'https://' + url_raw_cmd
            url_norm_cmd = urlunparse(urlparse(url_raw_cmd)._replace(query="")).lower().rstrip("/")
            if url_norm_cmd == link_info["url_norm"]:
                link_motivo = url_norm_cmd

        if not link_motivo:
            return await message.reply_text(f"O {target_user.mention} não enviou link recentemente. Sem motivo pra bloqueio.")

    # Caso 3: verifica se esse link já foi motivo de bloqueio antes
    if link_motivo in _bloqueios_por_link.get(target_id, set()):
        return await message.reply_text(f"O {target_user.mention} já foi bloqueado por esse link antes. Não vou bloquear de novo.")

    # --- Aplica o bloqueio ---
    is_self_block = message.from_user and target_id == message.from_user.id

    if not is_self_block:
        _uso_bloq[target_id] = [t for t in _uso_bloq[target_id] if agora - t < 86400]

        if len(_uso_bloq[target_id]) >= 3:
            return await message.reply_text(f"⚠️ O {target_user.mention} já tomou 3 castigos hoje! Deixa o coitado em paz, já sofreu demais por hoje.")

        _uso_bloq[target_id].append(agora)

    if not is_self_block and len(_uso_bloq[target_id]) >= 3:
        ts_list = _uso_bloq[target_id]
        if max(ts_list) - min(ts_list) <= 1200:
            duracao, tempo_str = 300, "5 minutos"
        else:
            duracao, tempo_str = 3600, "1 hora"
    else:
        duracao, tempo_str = 300, "5 minutos"

    _usuarios_bloqueados[target_id] = agora + duracao
    _bloqueios_por_link[target_id].add(link_motivo)

    msg = erro_aleatorio(ERROS_BLOQ_CMD, mention=target_user.mention, tempo=tempo_str)
    await message.reply_text(msg)

@app.on_message(filters.command("id"))
async def cmd_id(client, message):
    await message.reply_text(f"🆔 ID deste Chat: `{message.chat.id}`")

@app.on_message(filters.command("stats"))
async def cmd_stats(client, message):
    if not chat_autorizado(message.chat.id):
        return
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    ram = psutil.Process().memory_info().rss / (1024 * 1024)
    cpu = psutil.cpu_percent()
    txt = (
        f"**📊 Status**\n\n"
        f"⏱️ Uptime: `{uptime}`\n"
        f"📥 Downloads: `{DOWNLOAD_COUNT}`\n"
        f"💾 RAM: `{ram:.1f} MB`\n"
        f"⚡ CPU: `{cpu}%`"
    )
    await message.reply_text(txt)

@app.on_message(filters.command("ping"))
async def cmd_ping(client, message):
    if not chat_autorizado(message.chat.id):
        return
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    await message.reply_text(f"🏓 Pong! Bot online há `{uptime}`")

# -----------------------------------------
# SYNC DO MENU DE COMANDOS
# -----------------------------------------
async def atualizar_menu_comandos_super(client):
    """Atualiza o menu de comandos (botão /) no Telegram para o Super Bot."""
    try:
        lista_comandos = build_bot_commands(SUPER_COMMANDS)
        if BOT_TOKEN:
            await set_bot_commands_via_bot_api(BOT_TOKEN, SUPER_COMMANDS)
            await set_bot_commands_menu_button_via_bot_api(BOT_TOKEN)
        else:
            await client.set_bot_commands(lista_comandos)
        log.info(f"Menu de comandos do Super Bot atualizado no Telegram! ({len(lista_comandos)} comandos)")
        return True
    except Exception as e:
        log.error(f"Erro ao atualizar menu do Super Bot: {e}", exc_info=True)
        return False


@app.on_message(filters.command("sync"))
@admin_only
async def cmd_sync(client, message):
    if not chat_autorizado(message.chat.id):
        return
    sucesso = await atualizar_menu_comandos_super(client)
    if sucesso:
        await message.reply_text("✅ Menu do Telegram (botão /) atualizado com todos os comandos!")
    else:
        await message.reply_text("❌ Erro ao atualizar o menu. Veja os logs.")


@app.on_message(filters.command("ig_status"))
@admin_only
async def cmd_ig_status(client, message):
    if not chat_autorizado(message.chat.id):
        return
    try:
        contas = [
            ('principal', COOKIE_PATH),
            ('secundaria', SECONDARY_COOKIE_PATH),
        ]
        relatorios = []
        for nome, path in contas:
            relatorio = f"**Conta {nome}**\n" + await asyncio.to_thread(
                inspect_cookie_health, path
            )
            validacao = await validate_cookie_health(path)
            if validacao.get('valid'):
                relatorio += "\n\n🟢 **Validação real:** sessão CONFIRMADA com o Instagram."
            elif validacao.get('rate_limited'):
                relatorio += "\n\n🟡 **Validação real:** " + (
                    validacao.get('reason') or 'limite temporario do Instagram'
                )
            else:
                relatorio += "\n\n🔴 **Validação real:** ❌ " + (
                    validacao.get('reason') or 'sessão inválida'
                )
            relatorios.append(relatorio)
        relatorio = "\n\n──────────\n\n".join(relatorios)
        for parte in dividir_texto_longo(relatorio):
            await message.reply_text(parte)
    except Exception as e:
        log.error(f"Erro no comando ig_status: {e}")
        await message.reply_text(f"❌ Erro ao verificar cookies: {str(e)[:200]}")


@app.on_message(filters.command("ig_renew"))
@admin_only
async def cmd_ig_renew(client, message):
    if not chat_autorizado(message.chat.id):
        return
    aviso = await message.reply_text("🍪 Gerando cookies novos do Instagram... aguarde.")
    try:
        cookies = await asyncio.to_thread(_auto_login_and_save_cookies, COOKIE_PATH)
        if cookies:
            if "sessionid" in cookies:
                await aviso.edit_text("✅ **Cookies renovados com sucesso!**\nGenerei um `sessionid` novo. O download do Instagram deve voltar a funcionar.")
                if os.path.exists(COOKIE_PATH):
                    await asyncio.to_thread(reset_cookies_bad)
            else:
                await aviso.edit_text("⚠️ Login feito, mas o `sessionid` não foi encontrado nos cookies gerados. Verifique os logs.")
        else:
            await aviso.edit_text(
                "❌ Não foi possível gerar cookies novos.\n"
                "Verifique `IG_USERNAME` / `IG_PASSWORD` no .env e o 2FA da conta.\n"
                "Detalhes nos logs."
            )
    except Exception as e:
        log.error(f"Erro no comando ig_renew: {e}")
        await aviso.edit_text(f"❌ Erro ao renovar cookies: {str(e)[:200]}")


async def filtro_web_app_data(_, __, message):
    return bool(getattr(message, "web_app_data", None))


@app.on_message(filters.create(filtro_web_app_data))
async def handle_mini_app_data(client, message):
    if not chat_autorizado(message.chat.id):
        return
    try:
        payload = json.loads(message.web_app_data.data or "{}")
    except Exception:
        await message.reply_text("❌ Payload inválido do painel.")
        return

    kind = payload.get("kind")
    data = payload.get("data") or {}
    if kind == "execute_command":
        command = str(data.get("command", "")).strip().lstrip("/")
        if command not in command_names(SUPER_COMMANDS):
            await message.reply_text("❌ Comando desconhecido.")
            return
        await message.reply_text(f"Execute pelo chat: `/{command}`")
    else:
        await message.reply_text("✅ Painel recebido.")

@app.on_message(filters.command("retry"))
async def cmd_retry(client, message):
    """Responder a uma mensagem de erro do bot com /retry para tentar de novo."""
    if not chat_autorizado(message.chat.id):
        return
    if not message.reply_to_message:
        await message.reply_text(erro_aleatorio(ERROS_RETRY_SEM_RESPOSTA))
        return
    
    erro_msg_id = message.reply_to_message.id
    if erro_msg_id not in _retry_cache:
        await message.reply_text(erro_aleatorio(ERROS_RETRY_SEM_MSG))
        return
    
    url, usuario_orig, chat_id, original_msg_id = _retry_cache.pop(erro_msg_id)
    
    # Deleta a mensagem de erro antiga
    try:
        await message.reply_to_message.delete()
    except Exception:
        pass
    
    # Para retentar, modificamos a mensagem atual para fingir que é a original contendo a URL
    # e repassamos pro handler principal. Isso garante que todo o fluxo (X, IG, Motor) funcione.
    message.text = url
    message.id = original_msg_id
    await processar_links(client, message)

async def avisar_admin_cookies(client, motivo="expirados"):
    """Envia aviso ao admin quando cookies do Instagram falham."""
    if ADMIN_ID:
        try:
            await client.send_message(
                ADMIN_ID,
                f"🍪⚠️ **Alerta de Cookies Instagram**\n\n"
                f"Os cookies parecem estar {motivo}.\n"
                f"Atualize o arquivo: `{COOKIE_PATH}`"
            )
        except Exception as e:
            log.error(f"Falha ao avisar admin sobre cookies: {e}")

@app.on_callback_query(filters.regex(r"^(forcelong|cancellong)_([a-f0-9]{32})$"))
async def callback_long_video(client, callback_query):
    global DOWNLOAD_COUNT
    action = callback_query.matches[0].group(1)
    token = callback_query.matches[0].group(2)
    request = _long_requests.get(token)
    if request is None:
        await callback_query.answer('Confirmação expirada.', show_alert=True)
        return
    url, usuario_orig, original_message, created = request
    owner = getattr(original_message.from_user, 'id', None)
    if owner is None or callback_query.from_user.id != owner or callback_query.message.chat.id != original_message.chat.id:
        await callback_query.answer('Só quem solicitou pode confirmar ou cancelar.', show_alert=True)
        return
    _long_requests.pop(token, None)
    if time.monotonic() - created > 900:
        await callback_query.answer('Confirmação expirada.', show_alert=True)
        return
    await callback_query.answer()
    if action == 'cancellong':
        await callback_query.message.edit_text('🛑 Download cancelado.')
        return
    try:
        outcome = await executar_pipeline_social(
            client,
            original_message,
            url,
            usuario_orig,
            callback_query.message,
            force_long=True,
        )
        if outcome.item_count > 0 or outcome.text_only:
            async with DOWNLOAD_COUNT_LOCK:
                DOWNLOAD_COUNT += 1
    except DownloadCancelled:
        await callback_query.message.edit_text("🛑 Download cancelado.")
    except SocialMediaError as exc:
        await callback_query.message.edit_text(exc.public_message)
    except Exception:
        log.exception("Erro ao processar confirmacao de video longo")
        await callback_query.message.edit_text(erro_aleatorio(ERROS_INESPERADO))
    return



@app.on_callback_query(filters.regex(r"^canceldownload_([a-f0-9]{32})$"))
async def callback_cancelar_download(client, callback_query):
    msg_id = callback_query.matches[0].group(1)
    download = _downloads_cancelaveis.get(msg_id)
    if not download:
        await callback_query.answer("Esse download já terminou.", show_alert=True)
        return

    cancel_event, user_id, chat_id = download
    callback_user_id = getattr(getattr(callback_query, "from_user", None), "id", None)
    if user_id is None or callback_user_id != user_id or callback_query.message.chat.id != chat_id:
        await callback_query.answer("Só quem iniciou o download pode cancelar.", show_alert=True)
        return

    cancel_event.set()
    await callback_query.answer("Cancelando download...")
    try:
        await callback_query.message.edit_text("🛑 Cancelando download...")
    except Exception:
        pass

async def limpeza_periodica():
    """Remove arquivos órfãos da pasta downloads a cada 30 minutos."""
    while True:
        await asyncio.sleep(1800)  # 30 minutos
        try:
            agora = time.time()
            for token, request in list(_long_requests.items()):
                if time.monotonic() - request[3] > 900:
                    _long_requests.pop(token, None)
            removidos = 0
            for f in os.listdir(PASTA_DOWNLOADS):
                caminho = PASTA_DOWNLOADS / f
                if caminho.is_dir() and caminho.name.startswith('job_') and str(caminho.resolve()) not in ACTIVE_DIRECTORIES:
                    if agora - caminho.stat().st_mtime > 86400:
                        shutil.rmtree(caminho)
                    continue
                if caminho.is_file():
                    idade = agora - os.path.getmtime(caminho)
                    if idade > 86400:  # Conservative age for legacy flat files.
                        os.remove(caminho)
                        removidos += 1
            if removidos > 0:
                log.info(f"Limpeza periódica: {removidos} arquivos órfãos removidos.")
        except Exception as e:
            log.error(f"Erro na limpeza periódica: {e}")
            
        # Limpa caches em memória
        try:
            agora = time.time()
            _rate_limiter.prune(now=agora)
            _retry_cache.prune()
                
            # Nao libera downloads ativos apenas pelo volume: remove so locks velhos.
            agora_monotonic = time.monotonic()
            processamentos_expirados = [
                url for url, inicio in _processing_urls.items()
                if agora_monotonic - inicio > PROCESSING_URL_TTL
            ]
            for url in processamentos_expirados:
                _processing_urls.pop(url, None)
                
            # Failed URL cache
            agora = time.time()
            expirados = [u for u, ts in _failed_url_cache.items() if agora - ts > 600]
            for u in expirados:
                del _failed_url_cache[u]
                
            # Limpa _uso_bloq (limite diário)
            para_deletar_bloq = []
            for u, ts_list in _uso_bloq.items():
                _uso_bloq[u] = [t for t in ts_list if agora - t < 86400]
                if not _uso_bloq[u]:
                    para_deletar_bloq.append(u)
            for u in para_deletar_bloq:
                del _uso_bloq[u]
        except Exception:
            pass

# -----------------------------------------
# MÍDIA DE QUOTE
# -----------------------------------------
# CASTIGO DUPLICADO
# -----------------------------------------
async def enviar_aviso_duplicado(client, message, info_original: dict, repetido_db_info: dict = None, quem_enviou_ago: str = None):
    vezes = repetido_db_info.get("vezes", 1) if repetido_db_info else 1

    if repetido_db_info and repetido_db_info.get("primeiro_id"):
        quem_mandou_primeiro = f"[{repetido_db_info['primeiro_user']}](tg://user?id={repetido_db_info['primeiro_id']})"
    elif repetido_db_info and repetido_db_info.get("primeiro_user"):
        quem_mandou_primeiro = f"**{repetido_db_info['primeiro_user']}**"
    else:
        quem_mandou_primeiro = info_original["user"]

    quem_ago = quem_enviou_ago or info_original.get("agora", "alguém")

    texto = f"🚨 BOCA DE LEITE {quem_ago}! Esse link já foi enviado {vezes} vezes hoje no grupo (primeiro por {quem_mandou_primeiro}). Presta atenção no grupo!"

    lista_audios = ["boca-de-leite.ogg", "aids.ogg", "de-novo-cac.ogg"]
    for i, nome_audio in enumerate(lista_audios):
        caminho = Path(AUDIO_BOCA_LEITE_DIR) / nome_audio
        if caminho.exists():
            leg = texto if i == 0 else None
            await client.send_voice(message.chat.id, str(caminho), caption=leg, reply_to_message_id=message.id)
            await asyncio.sleep(0.7)

    if vezes >= 3:
        await metralhadora_stickers(client, message.chat.id)

def _social_pipeline_config() -> SocialPipelineConfig:
    return SocialPipelineConfig(
        download_root=PASTA_DOWNLOADS,
        max_media_bytes=LIMITE_TAMANHO,
        download_timeout=max(30, get_int_env("YTDLP_DOWNLOAD_TIMEOUT", 7200)),
        duration_limit=LIMITE_DURACAO,
        media_download_concurrency=IG_MEDIA_DOWNLOAD_CONCURRENCY,
        playlist_limit=max(1, min(get_int_env("GENERIC_MAX_ITEMS", 20), 50)),
        instagram_cookie_path=COOKIE_PATH,
        instagram_secondary_cookie_path=SECONDARY_COOKIE_PATH,
    )


async def executar_pipeline_social(
    client,
    message,
    url,
    usuario,
    status,
    *,
    force_long=False,
):
    """Adapta estado do Telegram ao pipeline modular de redes sociais."""
    global _fila_espera
    cancel_event = threading.Event()
    requester_id = getattr(getattr(message, "from_user", None), "id", None)
    token = uuid.uuid4().hex
    _downloads_cancelaveis[token] = (
        cancel_event,
        requester_id,
        message.chat.id,
    )
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton(
        "🛑 Cancelar download",
        callback_data=f"canceldownload_{token}",
    )]])
    entrou_fila = False
    if semaforo.locked():
        async with _fila_lock:
            _fila_espera += 1
            entrou_fila = True
            position = _fila_espera
        await status.edit_text(f"💬 Na fila... Posição: {position}")
    try:
        async with semaforo:
            if entrou_fila:
                async with _fila_lock:
                    _fila_espera = max(0, _fila_espera - 1)
                entrou_fila = False
            await status.edit_text(
                "⬇️ Preparando extração...",
                reply_markup=cancel_markup,
            )
            pipeline = SocialMediaPipeline(
                client=client,
                session=await get_http_session(),
                config=_social_pipeline_config(),
                progress=_progresso_upload(status),
            )
            return await pipeline.deliver(
                message=message,
                url=url,
                requested_by=usuario,
                status=status,
                cancel_event=cancel_event,
                reply_markup=cancel_markup,
                force_long=force_long,
                long_video_callback=avisar_video_longo,
            )
    finally:
        _downloads_cancelaveis.pop(token, None)
        if entrou_fila:
            async with _fila_lock:
                _fila_espera = max(0, _fila_espera - 1)


# -----------------------------------------
# ESCUTA DE MENSAGENS
# -----------------------------------------
COMANDOS = set(command_names(SUPER_COMMANDS))

@app.on_message(filters.text & ~filters.command(list(COMANDOS)))
@managed_downloads
async def processar_links(client, message):
    global DOWNLOAD_COUNT
    texto = message.text
    if not texto:
        return
    if not chat_autorizado(message.chat.id):
        return

    if message.from_user:
        nome = message.from_user.first_name or "Membro"
        u_name = message.from_user.username
        usuario = f"{nome} (@{u_name})" if u_name else nome
        user_id = message.from_user.id
    else:
        nome = "Membro"
        usuario = "Membro"
        user_id = 0

    # Aceita URL completa ou nua, mas somente em hosts sociais permitidos.
    url_raw = extract_supported_url(texto, DOMINIOS_PERMITIDOS)
    repetido_db = False
    info_db = {}

    if url_raw:
        agora_atual = time.time()
        if user_id in _usuarios_bloqueados:
            if agora_atual < _usuarios_bloqueados[user_id]:
                tr = int(_usuarios_bloqueados[user_id] - agora_atual)
                tempo_str = f"{tr // 60}min {tr % 60}s"
                msg_erro = erro_aleatorio(ERROS_BLOQ_TENTATIVA, mention=message.from_user.mention, tempo=tempo_str)
                await message.reply_text(msg_erro)
                return
            else:
                del _usuarios_bloqueados[user_id]

        if user_id and not verificar_rate_limit(user_id):
            aviso = await message.reply_text(erro_aleatorio(ERROS_RATE_LIMIT))
            await asyncio.sleep(5)
            try:
                await aviso.delete()
            except Exception:
                pass
            return

        # Apenas CHECA se é duplicado (sem registrar). Registro acontece só após sucesso.
        url_norm = normalizar_link_social(url_raw)

        repetido_db, info_db = await asyncio.to_thread(db.checar_link, url_norm, message.chat.id)

        # Registra o último link enviado pelo usuário (para validar /bloq)
        _ultimo_link_por_usuario[user_id] = {
            "url_norm": url_norm,
            "url_raw": url_raw,
            "timestamp": time.time()
        }

        # Race condition lock
        async with _processing_lock:
            if url_norm in _processing_urls:
                await message.reply_text(erro_aleatorio(ERROS_LINK_PROCESSANDO))
                return
            _processing_urls[url_norm] = time.monotonic()

    if not url_raw:
        return

    platform = detect_platform(url_raw)

    # Perfis possuem respostas proprias; o registro recebe apenas conteudo.
    if platform == "twitter" and not match_tweet_url(url_raw) and match_profile_url(url_raw):
        try:
            await responder_perfil_x(client, message, url_raw)
        finally:
            async with _processing_lock:
                _processing_urls.pop(url_norm, None)
        return

    if platform == "instagram" and get_profile_username(url_raw):
        try:
            await responder_perfil_instagram(client, message, url_raw)
        finally:
            async with _processing_lock:
                _processing_urls.pop(url_norm, None)
        return

    if platform == "instagram":
        agora_ts = time.time()
        if url_norm in _failed_url_cache and agora_ts - _failed_url_cache[url_norm] < 300:
            restante = int(300 - (agora_ts - _failed_url_cache[url_norm]))
            tempo_str = f"{restante // 60}min {restante % 60}s"
            await message.reply_text(erro_aleatorio(ERROS_COOLDOWN, tempo=tempo_str))
            async with _processing_lock:
                _processing_urls.pop(url_norm, None)
            return

    status_text = {
        "twitter": "🐦 Puxando dados do X...",
        "instagram": "⏳ Baixando do Instagram...",
        "facebook": "📘 Carregando publicação do Facebook...",
    }.get(platform, "⏳ Puxando mídia original...")
    status = await message.reply_text(status_text)
    try:
        outcome = await executar_pipeline_social(
            client,
            message,
            url_raw,
            usuario,
            status,
        )
        if outcome.skipped:
            return
        delivered = outcome.item_count > 0 or outcome.text_only
        if not delivered:
            await status.edit_text(UNAVAILABLE_NOTICE)
            return

        async with DOWNLOAD_COUNT_LOCK:
            DOWNLOAD_COUNT += 1
        if platform == "instagram":
            _failed_url_cache.pop(url_norm, None)
            if (
                outcome.bundle.metadata.get("_primary_cookie_failed")
                and outcome.bundle.metadata.get("_cookie_source") == "secondary"
            ):
                await avisar_admin_cookies(
                    client,
                    "com falha; a conta secundaria assumiu o download",
                )
        log.info(
            "pipeline social concluido platform=%s items=%d text_only=%s partial=%s",
            outcome.platform,
            outcome.item_count,
            outcome.text_only,
            outcome.partial,
        )

        if platform != "youtube":
            repetido_db, info_db = await asyncio.to_thread(
                db.registrar_link_e_checar,
                url_norm,
                message.chat.id,
                nome,
                user_id,
            )
            if repetido_db:
                await enviar_aviso_duplicado(
                    client, message, {}, info_db, usuario
                )
    except DownloadCancelled:
        await status.edit_text("🛑 Download cancelado.")
    except MediaTooLong:
        await avisar_video_longo(status, url_raw, usuario, message)
    except AuthenticationRequired as exc:
        if platform == "facebook":
            await status.edit_text(ACCESS_NOTICE)
        elif platform == "instagram":
            motivo = get_cookie_failure_reason()
            await avisar_admin_cookies(client, f"expirados ou invalidos ({motivo})")
            await status.edit_text(f"{erro_aleatorio(ERROS_INSTAGRAM)}\n\n🔒 {motivo}")
        else:
            await status.edit_text(exc.public_message)
        _retry_cache[status.id] = (url_raw, usuario, message.chat.id, message.id)
        if platform == "instagram":
            _failed_url_cache[url_norm] = time.time()
    except MediaTooLarge as exc:
        await status.edit_text(exc.public_message)
        _retry_cache[status.id] = (url_raw, usuario, message.chat.id, message.id)
    except SocialMediaError as exc:
        log.warning(
            "pipeline social falhou platform=%s stage=%s error_type=%s",
            platform,
            exc.stage or "unknown",
            type(exc).__name__,
        )
        public_message = {
            "twitter": erro_aleatorio(ERROS_X),
            "instagram": erro_aleatorio(ERROS_INSTAGRAM),
            "facebook": UNAVAILABLE_NOTICE,
        }.get(platform, exc.public_message)
        await status.edit_text(public_message)
        _retry_cache[status.id] = (url_raw, usuario, message.chat.id, message.id)
        if platform == "instagram":
            _failed_url_cache[url_norm] = time.time()
    except Exception as exc:
        log.exception(
            "erro inesperado no pipeline social platform=%s error_type=%s",
            platform,
            type(exc).__name__,
        )
        await status.edit_text(erro_aleatorio(ERROS_INESPERADO))
        _retry_cache[status.id] = (url_raw, usuario, message.chat.id, message.id)
        if platform == "instagram":
            _failed_url_cache[url_norm] = time.time()
    finally:
        async with _processing_lock:
            _processing_urls.pop(url_norm, None)

# -----------------------------------------
# NOTIFICAÇÃO DE ATUALIZAÇÃO
# -----------------------------------------
async def notificar_atualizacao():
    """Envia notificação nos grupos quando o bot reinicia após um git pull com mudanças."""
    await asyncio.sleep(5)  # Aguarda a conexão do bot estabilizar
    changelog_file = Path(RAIZ) / "data" / "update_superbot.json"
    if not changelog_file.exists():
        return
    try:
        with open(changelog_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        commits = data.get('commits', [])
        if not commits:
            changelog_file.unlink(missing_ok=True)
            return

        txt = "🔄 **Super Bot Atualizado!** 🚀\n\n"
        txt += "📋 **Mudanças nesta atualização:**\n"
        for c in commits:
            txt += f"• `{c['hash']}` — {c['message']}\n"
        txt += f"\n🕐 {data.get('updated_at', 'N/A')}"

        partes = dividir_texto_longo(txt)
        enviados = 0
        for grupo_id in GRUPOS_AUTORIZADOS:
            try:
                for parte in partes:
                    await app.send_message(grupo_id, parte)
                enviados += 1
            except Exception as e:
                log.error(f"Erro ao enviar notificação de update para {grupo_id}: {e}")

        # Fallback: se não há grupos autorizados, envia para o admin
        if not GRUPOS_AUTORIZADOS and ADMIN_ID:
            try:
                for parte in partes:
                    await app.send_message(ADMIN_ID, parte)
                enviados += 1
            except Exception as e:
                log.error(f"Erro ao enviar notificação de update para admin: {e}")

        changelog_file.unlink(missing_ok=True)
        log.info(f"Notificação de atualização enviada para {enviados} chat(s).")
    except Exception as e:
        log.error(f"Erro ao processar changelog de atualização: {e}")

# -----------------------------------------
# INICIALIZACAO
# -----------------------------------------
if __name__ != "__main__" and not _runtime_loop.is_running():
    # Pyrogram agenda o registro dos decorators no loop do Client. Em imports de
    # ferramentas/testes, executamos um ciclo curto para nao deixar tasks orfas.
    _runtime_loop.run_until_complete(asyncio.sleep(0))

if __name__ == "__main__":
    db.init_db()
    PASTA_DOWNLOADS.mkdir(parents=True, exist_ok=True)

    arquivos_apagados = 0
    for f in os.listdir(PASTA_DOWNLOADS):
        try:
            os.remove(PASTA_DOWNLOADS / f)
            arquivos_apagados += 1
        except Exception:
            pass
    if arquivos_apagados > 0:
        log.info(f"Limpeza inicial: {arquivos_apagados} arquivos orfaos deletados.")

    if os.path.exists(COOKIE_PATH):
        log.info("Cookies do Instagram encontrados. Download autenticado ativado.")
    else:
        log.warning("Cookies do Instagram NAO encontrados. Veja COOKIES_SETUP.md")

    if GRUPOS_AUTORIZADOS:
        log.info(f"Grupos permitidos: {GRUPOS_AUTORIZADOS}")

    log.info("Super Bot iniciado!")

    # --- Canário de conectividade ---
    # O Pyrogram às vezes perde o socket MTProto (ex: o DC poda a conexão ou a
    # rede cai) e fica preso apenas mandando keepalives que falham
    # ("socket.send() raised exception"), sem se recuperar sozinho. Este canário
    # faz um Ping real ao DC em background: se falhar N vezes seguidas, forçamos
    # o encerramento do processo para o supervisor/systemd recriá-lo de forma limpa.
    CANARIO_INTERVALO = 15     # segundos entre pings
    CANARIO_FALHAS = 4         # falhas consecutivas antes de reiniciar
    _falhas_ping = [0]

    async def _canario_conectividade():
        while True:
            await asyncio.sleep(CANARIO_INTERVALO)
            try:
                await app.invoke(
                    raw.functions.Ping(ping_id=random.randint(1, pow(2, 31) - 1))
                )
                _falhas_ping[0] = 0
            except Exception as e:
                _falhas_ping[0] += 1
                log.warning(f"Canário: ping ao DC falhou ({_falhas_ping[0]}/{CANARIO_FALHAS}): {e}")
                if _falhas_ping[0] >= CANARIO_FALHAS:
                    log.error("Canário: conexão com o Telegram caiu. Reiniciando o bot...")
                    os._exit(1)

    async def _rodar_with_canario():
        await app.start()
        tarefas_fundo = [
            asyncio.create_task(_canario_conectividade(), name="canario-conectividade"),
            asyncio.create_task(notificar_atualizacao(), name="notificar-atualizacao"),
            asyncio.create_task(limpeza_periodica(), name="limpeza-periodica"),
        ]
        try:
            await idle()
        finally:
            for tarefa in tarefas_fundo:
                tarefa.cancel()
            await asyncio.gather(*tarefas_fundo, return_exceptions=True)
            await app.stop()

    try:
        _runtime_loop.run_until_complete(_rodar_with_canario())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error(f"Erro fatal na execução do bot: {e}", exc_info=True)
        os._exit(1)
    finally:
        try:
            _runtime_loop.run_until_complete(close_http_session())
        except Exception:
            pass
