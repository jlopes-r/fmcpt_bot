# Teste do sistema de atualização
import sys
import os
import re
import json
import time
import random
import asyncio
import logging
import subprocess
import psutil
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse
from collections import defaultdict
from logging.handlers import RotatingFileHandler
import yt_dlp
import aiohttp

from pyrogram import Client, filters, idle, raw
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton
try:
    from pyrogram.file_id import FileId, FileType
except ImportError:
    FileId = None
    FileType = None
from dotenv import load_dotenv
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
from packages.url_utils import normalizar_url
from apps.telegram_bot.downloaders import (
    limite_duracao_filter,
    FORMATO_MP4_H264 as _FORMATO_MP4_H264,
    baixar_com_ytdlp as _baixar_com_ytdlp,
    baixar_url_limitado as _baixar_url_limitado,
)
from apps.telegram_bot.duplicates import normalizar_link_social
from apps.telegram_bot.instagram import (
    download_instagram,
    fetch_instagram_profile,
    get_profile_username,
    detect_profile_privado as _detect_profile_privado,
    cookies_known_bad,
    get_cookie_failure_reason,
    _auto_login_and_save_cookies,
    inspect_cookie_health,
    validate_cookie_health,
    reset_cookies_bad,
)
from apps.telegram_bot.instagram_profile_card import gerar_card as _gerar_card_perfil
from apps.telegram_bot.media_utils import detectar_extensao as _detectar_extensao, progresso_upload as _progresso_upload
from apps.telegram_bot.text_utils import dividir_texto_longo, limpar_texto, montar_legenda
from apps.telegram_bot.translator import nome_idioma, traduzir_com_detalhes, traduzir_se_necessario
from apps.telegram_bot.twitter import build_vxtwitter_url, build_fxtwitter_url, match_tweet_url, match_profile_url, build_profile_url, build_follow_info_url

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
LIMITE_TAMANHO = 2_000_000_000  # Aumentado para 2GB (limite do Telegram para bots via MTProto)
MAX_DOWNLOADS = 3
MAX_RETRIES = 2
RATE_LIMIT = 10
RATE_JANELA = 60

AUDIO_BOCA_LEITE_DIR = os.path.join(RAIZ, "assets", "audios")
PASTA_DOWNLOADS = DOWNLOADS_DIR
COOKIE_PATH = str(instagram_cookie_path())
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
    "pinterest.com", "pin.it"
]

# -----------------------------------------
# POOL DE SESSÕES HTTP REUTILIZÁVEIS
# Reutilizar ClientSession evita o custo de handshake TLS/DNS
# a cada chamada (principalmente em picos de uso).
# -----------------------------------------
_http_session: aiohttp.ClientSession | None = None
_http_session_lock = asyncio.Lock()
_HTTP_TIMEOUT_RATE = 5      # requisições leves (encurtar url, checagens)

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
# CACHE DO ENCURTADOR DE URL
# -----------------------------------------
_encurtada_cache: dict[str, str] = {}
_ENCRTADA_CACHE_MAX = 2000

async def fetch_short(url: str) -> str:
    """Chama o is.gd uma única vez por URL, com cache em memória."""
    if url in _encurtada_cache:
        return _encurtada_cache[url]
    curta = url
    try:
        session = await get_http_session()
        async with session.get(
            f"https://is.gd/create.php?format=simple&url={url}",
            timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_RATE),
        ) as r:
            if r.status == 200:
                curta = (await r.text()).strip() or url
    except Exception:
        pass
    if len(_encurtada_cache) >= _ENCRTADA_CACHE_MAX:
        _encurtada_cache.clear()
    _encurtada_cache[url] = curta
    return curta

# -----------------------------------------
from apps.telegram_bot.mensagens_erro import (
    ERROS_RATE_LIMIT,
    ERROS_VIDEO_LONGO,
    ERROS_ARQUIVO_GRANDE,
    ERROS_EXTRACAO,
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
_historico_uso = defaultdict(list)
_fila_espera = 0
_fila_lock = asyncio.Lock()
_retry_cache = {}  # msg_erro_id -> (url, usuario, chat_id, original_msg_id)
_failed_url_cache = {}  # url_norm -> timestamp (cooldown para URLs que falharam recentemente)
_processing_urls = set()  # URLs em processamento (evita downloads duplicados simultâneos)
_processing_lock = asyncio.Lock()
_usuarios_bloqueados = {}  # user_id -> timestamp (cooldown de castigo de 5min)
_uso_bloq = defaultdict(list)  # admin_id -> [timestamps dos blocks aplicados hoje]
_ultimo_link_por_usuario = {}  # user_id -> {"url_norm": str, "url_raw": str, "timestamp": float}
_bloqueios_por_link = defaultdict(set)  # user_id -> set de url_norms que já causaram bloqueio

# -----------------------------------------
# LOGGING
# -----------------------------------------
configure_rotating_logging(LOG_DIR, "bot.log")

log = logging.getLogger("SuperBot")

# -----------------------------------------
# CLIENTE
# -----------------------------------------
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
def url_permitida(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if host in ("localhost", "127.0.0.1", "0.0.0.0"):
            return False
        return any(host == d or host.endswith(f".{d}") for d in DOMINIOS_PERMITIDOS)
    except Exception:
        return False

def verificar_rate_limit(user_id: int) -> bool:
    agora = time.time()
    _historico_uso[user_id] = [t for t in _historico_uso[user_id] if agora - t < RATE_JANELA]
    if len(_historico_uso[user_id]) >= RATE_LIMIT:
        return False
    _historico_uso[user_id].append(agora)
    return True

def chat_autorizado(chat_id: int) -> bool:
    if not GRUPOS_AUTORIZADOS:
        return True
    return chat_id in GRUPOS_AUTORIZADOS

async def encurtar_url(url: str) -> str:
    return await fetch_short(url)

# -----------------------------------------
# MOTOR DE DOWNLOAD
# -----------------------------------------
_filtro_duracao = limite_duracao_filter(LIMITE_DURACAO)

async def avisar_video_longo(msg_espera, url, usuario, message):
    texto_aviso = erro_aleatorio(ERROS_VIDEO_LONGO, min=LIMITE_DURACAO // 60)
    texto_aviso += "\n\n⚠️ Tem certeza que quer baixar essa merda gigante? Pode demorar pra caralho e bugar o bot."

    botoes = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim, baixa essa porra", callback_data=f"forcelong_{msg_espera.id}")],
        [InlineKeyboardButton("❌ Não, foda-se", callback_data=f"cancellong_{msg_espera.id}")]
    ])

    await msg_espera.edit_text(texto_aviso, reply_markup=botoes)
    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)

def _foi_pulado_por_duracao(item: dict) -> bool:
    duracao = item.get('duration') or 0
    return bool(duracao and duracao > LIMITE_DURACAO)

def _converter_para_jpg(data: bytes, ext: str) -> tuple[bytes, str]:
    """Converte webp/heic/heif para jpg. Roda em thread (CPU-bound)."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(data))
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=95)
    return buf.getvalue(), 'jpg'


async def extrair_e_enviar_midia(client, message, url, usuario, msg_espera, force_long=False):
    """Motor de download genérico. Retorna True se obteve sucesso."""
    global DOWNLOAD_COUNT, _fila_espera
    arquivos_para_deletar = []
    entrou_fila = False
    if semaforo.locked():
        async with _fila_lock:
            _fila_espera += 1
            entrou_fila = True
            pos = _fila_espera
        await msg_espera.edit_text(f"💬 Na fila... Posição: {pos}")
        
    async with semaforo:
        if entrou_fila:
            async with _fila_lock:
                _fila_espera -= 1
        for tentativa in range(1, MAX_RETRIES + 1):
            try:
                if tentativa > 1:
                    await msg_espera.edit_text(f"🔄 Tentativa {tentativa}/{MAX_RETRIES}...")
                    await asyncio.sleep(2)

                ydl_opts = {
                    'format': _FORMATO_MP4_H264,
                    'outtmpl': '%(id)s.%(ext)s',
                    'paths': {'home': str(PASTA_DOWNLOADS)},
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': False,
                    'max_filesize': LIMITE_TAMANHO,
                    'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
                }
                
                if not force_long:
                    ydl_opts['match_filter'] = _filtro_duracao

                if any(d in url for d in ["instagram.com", "instagr.am", "threads.net"]):
                    ydl_opts['http_headers'] = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                        'Referer': 'https://www.instagram.com/',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }

                info = await _baixar_com_ytdlp(url, ydl_opts, msg_espera=msg_espera)

                midias = info.get('entries', [info])
                lista_telegram = []

                legenda_base = limpar_texto(info.get('title') or info.get('description') or "")
                legenda_base = await asyncio.to_thread(traduzir_se_necessario, legenda_base)
                autor = info.get('uploader') or info.get('channel') or "Autor"
                legenda_final = montar_legenda(legenda_base, autor, usuario)

                await msg_espera.edit_text(f"✨ Extraído! Enviando {'album' if len(midias) > 1 else 'arquivo'}...")

                for i, item in enumerate(midias):
                    path = None
                    if 'requested_downloads' in item:
                        for dl in item['requested_downloads']:
                            if 'filepath' in dl and os.path.exists(dl['filepath']):
                                path = dl['filepath']
                                break
                    if not path:
                        path = item.get('filepath')
                        if not path or not os.path.exists(path):
                            # Fallback: procura o arquivo na pasta de downloads pelo ID do vídeo
                            video_id = item.get('id', '')
                            if video_id:
                                import glob
                                padrao = str(PASTA_DOWNLOADS / f"{video_id}.*")
                                encontrados = glob.glob(padrao)
                                if encontrados:
                                    path = encontrados[0]
                                    log.info(f"Fallback: arquivo encontrado via glob: {path}")
                            if not path or not os.path.exists(path):
                                # Verifica se foi filtrado por tamanho antes de dar erro
                                filesize = item.get('filesize') or item.get('filesize_approx')
                                if filesize and filesize > LIMITE_TAMANHO:
                                    raise Exception(f"Arquivo muito grande ({filesize / 1024 / 1024:.1f}MB). O limite é de 2GB.")
                                # yt-dlp novo não lança exceção quando o match_filter pula
                                # o vídeo: retorna o info dict sem requested_downloads.
                                if not force_long and _foi_pulado_por_duracao(item):
                                    log.info(f"Vídeo pulado pelo filtro de duração: {item.get('id')} ({item.get('duration')}s)")
                                    await avisar_video_longo(msg_espera, url, usuario, message)
                                    return False
                                log.warning(f"Arquivo não encontrado para item {i}: filepath={item.get('filepath')}, id={item.get('id')}, requested_downloads={item.get('requested_downloads')}")
                                continue

                    arquivos_para_deletar.append(path)
                    ext = path.lower().split('.')[-1]
                    cap = legenda_final if i == 0 else ""

                    if ext in ['jpg', 'jpeg', 'png', 'webp']:
                        lista_telegram.append(InputMediaPhoto(path, caption=cap))
                    else:
                        lista_telegram.append(InputMediaVideo(path, caption=cap, supports_streaming=True))

                if not lista_telegram:
                    # Log detalhado para diagnóstico
                    log.error(f"Nenhum arquivo válido encontrado. URL={url}, midias={len(midias)}, downloads_dir={list(PASTA_DOWNLOADS.iterdir()) if PASTA_DOWNLOADS.exists() else 'N/A'}")
                    raise Exception("Nenhum arquivo valido encontrado.")

                if len(lista_telegram) == 1:
                    midia = lista_telegram[0]
                    if isinstance(midia, InputMediaPhoto):
                        await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                    else:
                        await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                else:
                    for i in range(0, len(lista_telegram), 10):
                        lote = lista_telegram[i:i+10]
                        await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                        if len(lista_telegram) > 10:
                            await asyncio.sleep(2)

                async with DOWNLOAD_COUNT_LOCK:
                    DOWNLOAD_COUNT += 1
                log.info(f"Sucesso: {url} ({len(lista_telegram)} itens)")
                try:
                    await msg_espera.delete()
                except Exception:
                    pass
                return True

            except yt_dlp.utils.DownloadError as e:
                erro_str = str(e)
                # Erros de limite não fazem sentido tentar de novo
                if "Video tem" in erro_str:
                    await avisar_video_longo(msg_espera, url, usuario, message)
                    return False
                elif "File is larger" in erro_str:
                    await msg_espera.edit_text(erro_aleatorio(ERROS_ARQUIVO_GRANDE))
                    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
                    return False
                # Outros erros: tenta de novo se tiver tentativas restantes
                if tentativa >= MAX_RETRIES:
                    log.error(f"Erro yt-dlp (após {MAX_RETRIES} tentativas): {e}")
                    try:
                        await msg_espera.edit_text(erro_aleatorio(ERROS_EXTRACAO))
                        _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
                    except Exception:
                        pass
                    return False
            except Exception as e:
                if tentativa >= MAX_RETRIES:
                    log.error(f"Erro Motor (após {MAX_RETRIES} tentativas): {e}")
                    try:
                        await msg_espera.edit_text(erro_aleatorio(ERROS_INESPERADO))
                        _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
                    except Exception:
                        pass
                    return False
            finally:
                for p in arquivos_para_deletar:
                    if os.path.exists(p):
                        os.remove(p)
                arquivos_para_deletar.clear()
    return False


# -----------------------------------------
# INSTAGRAM HANDLER
# -----------------------------------------
async def processar_instagram(client, message, url, usuario, msg_espera, link_duplicado=None):
    """Handler dedicado para Instagram com cookies + embed fallback. Retorna True se obteve sucesso."""
    global DOWNLOAD_COUNT
    arquivos_para_deletar = []
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            if tentativa > 1:
                await msg_espera.edit_text(f"🔄 Instagram: Tentativa {tentativa}/{MAX_RETRIES}...")
                await asyncio.sleep(2)

            result = await download_instagram(url, COOKIE_PATH, str(PASTA_DOWNLOADS))

            if not result:
                if tentativa >= MAX_RETRIES:
                    msg_base = erro_aleatorio(ERROS_INSTAGRAM)
                    if cookies_known_bad:
                        motivo = get_cookie_failure_reason()
                        await avisar_admin_cookies(client, f"expirados ({motivo})")
                        msg_detalhada = (
                            f"{msg_base}\n\n"
                            f"🔒 **Motivo técnico:** Instagram exigiu autenticação/verificação.\n"
                            f"📌 **Detalhe:** `{motivo}`\n"
                            f"💡 **Solução:** Atualize o arquivo de cookies (`data/instagram_cookies.txt`)."
                        )
                    else:
                        msg_detalhada = msg_base

                    await msg_espera.edit_text(msg_detalhada)
                    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
                    return False
                continue

            legenda_base = limpar_texto(result.get('title', ''))
            autor = result.get('uploader', 'Autor')
            legenda_final = montar_legenda(legenda_base, autor, usuario, emoji="📸")
            
            lista_telegram = []

            if 'files' in result:
                midias_baixadas = result['files']
                for i, path in enumerate(midias_baixadas):
                    if not os.path.exists(path):
                        continue
                    arquivos_para_deletar.append(path)
                    ext = path.lower().split('.')[-1]
                    cap = legenda_final if i == 0 else ""
                    if ext in ['jpg', 'jpeg', 'png', 'webp']:
                        lista_telegram.append(InputMediaPhoto(path, caption=cap))
                    else:
                        lista_telegram.append(InputMediaVideo(path, caption=cap, supports_streaming=True))

            elif 'urls' in result:
                midias_urls = result['urls']
                await msg_espera.edit_text(f"✨ Extraído! Baixando {len(midias_urls)} {'item' if len(midias_urls) == 1 else 'itens'}...")
                
                # Headers para CDN do Instagram (evita 403)
                cdn_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                    'Referer': 'https://www.instagram.com/',
                }
                session = await get_http_session()

                async def _baixar_midia_ig(i, m_url):
                    """Baixa uma mídia do carrossel e prepara o InputMedia."""
                    try:
                        async with session.get(m_url, headers=cdn_headers) as response:
                            if response.status != 200:
                                log.warning(f"Falha ao baixar URL do Instagram ({response.status}): {m_url[:100]}")
                                return None
                            content_type = response.headers.get('Content-Type', '')
                            ext = _detectar_extensao(m_url, content_type)
                            # Stream com limite de tamanho (aborta se passar de 2GB)
                            if response.content_length and response.content_length > LIMITE_TAMANHO:
                                log.warning(f"Mídia IG muito grande ({response.content_length} bytes): {m_url[:100]}")
                                return None
                            dados_pendentes = bytearray()
                            async for chunk in response.content.iter_chunked(1024 * 1024):
                                dados_pendentes.extend(chunk)
                                if len(dados_pendentes) > LIMITE_TAMANHO:
                                    raise Exception(f"Mídia IG muito grande (> {LIMITE_TAMANHO} bytes)")
                            data = bytes(dados_pendentes)

                            # Converte webp/heic → jpg (Telegram rejeita esses formatos como foto)
                            if ext in ('webp', 'heic', 'heif'):
                                try:
                                    data, ext = await asyncio.to_thread(_converter_para_jpg, data, ext)
                                    log.info("   Convertido para jpg para compatibilidade Telegram")
                                except Exception as conv_err:
                                    log.warning(f"Falha ao converter para jpg: {conv_err}")
                                    ext = 'jpg'  # Tenta enviar mesmo assim

                            caminho_temp = PASTA_DOWNLOADS / f"temp_insta_{message.id}_{i}.{ext}"
                            with open(caminho_temp, 'wb') as f:
                                f.write(data)

                            arquivos_para_deletar.append(str(caminho_temp))
                            cap = legenda_final if i == 0 else ""
                            is_video = ext in ('mp4', 'mov', 'm4v', 'webm')
                            if is_video:
                                item = InputMediaVideo(str(caminho_temp), caption=cap, supports_streaming=True)
                            else:
                                item = InputMediaPhoto(str(caminho_temp), caption=cap)
                            log.info(f"   Mídia {i+1}/{len(midias_urls)} baixada: ext={ext}, size={len(data)} bytes")
                            return item
                    except Exception as e:
                        log.error(f"Erro ao baixar midia individual do Instagram: {e}")
                        return None

                resultados = await asyncio.gather(
                    *(_baixar_midia_ig(i, m_url) for i, m_url in enumerate(midias_urls))
                )
                lista_telegram = [r for r in resultados if r is not None]

            # Envio parcial: envia o que conseguiu, mesmo se nem tudo foi baixado
            if lista_telegram:
                if len(lista_telegram) == 1:
                    midia = lista_telegram[0]
                    if isinstance(midia, InputMediaPhoto):
                        await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                    else:
                        await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                else:
                    for i in range(0, len(lista_telegram), 10):
                        lote = lista_telegram[i:i+10]
                        await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                        if len(lista_telegram) > 10:
                            await asyncio.sleep(2)

                async with DOWNLOAD_COUNT_LOCK:
                    DOWNLOAD_COUNT += 1
                log.info(f"Instagram sucesso (upload): {url} ({len(lista_telegram)} itens)")
                try:
                    await msg_espera.delete()
                except Exception:
                    pass
                return True
            else:
                raise Exception("Nenhum arquivo válido encontrado ou baixado.")

        except Exception as e:
            if tentativa >= MAX_RETRIES:
                log.error(f"Erro Instagram handler (após {MAX_RETRIES} tentativas): {e}")
                msg_base = erro_aleatorio(ERROS_INSTAGRAM)
                
                if cookies_known_bad:
                    motivo = get_cookie_failure_reason()
                    await avisar_admin_cookies(client, f"expirados ou inválidos ({motivo})")
                    msg_detalhada = (
                        f"{msg_base}\n\n"
                        f"🔒 **Motivo técnico:** Instagram bloqueou por autenticação.\n"
                        f"📌 **Erro:** `{motivo}`\n"
                        f"💡 **Solução:** Renovar o `instagram_cookies.txt` no servidor."
                    )
                elif "login" in str(e).lower() or "cookie" in str(e).lower() or "checkpoint" in str(e).lower():
                    await avisar_admin_cookies(client, "expirados ou confirmação pendente")
                    msg_detalhada = (
                        f"{msg_base}\n\n"
                        f"🔒 **Motivo técnico:** O Instagram exigiu login/confirmação.\n"
                        f"📌 **Erro:** `{str(e)[:150]}`"
                    )
                else:
                    msg_detalhada = msg_base

                try:
                    await msg_espera.edit_text(msg_detalhada)
                    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
                except Exception:
                    pass
                return False
        finally:
            for p in arquivos_para_deletar:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception as e:
                        log.error(f"Erro ao deletar arquivo temporário {p}: {e}")
            arquivos_para_deletar.clear()
    return False


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
    privacidade = "Privado" if profile.get("is_private") else "Publico"
    bio = profile.get("biography") or "Sem bio."

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


async def responder_perfil_instagram(client, message, url):
    profile = await fetch_instagram_profile(url, COOKIE_PATH)
    if not profile:
        privado = await _detect_profile_privado(url, COOKIE_PATH)
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
    photo_url = profile.get("profile_pic_url") or ""
    if photo_url:
        try:
            dl_session = await get_http_session()
            async with dl_session.get(
                photo_url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200 and (not resp.content_length or resp.content_length <= LIMITE_TAMANHO):
                    foto_bytes = await resp.read()
        except Exception as e:
            log.warning("Falha ao baixar foto do perfil Instagram: %s", str(e)[:150])

    # Monta o card estilizado (roda em thread para não travar o loop)
    try:
        card_bytes = await asyncio.to_thread(_gerar_card_perfil, profile, foto_bytes)
        card_path = PASTA_DOWNLOADS / f"card_insta_{message.id}.png"
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
            os.remove(card_path)
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
async def cmd_sync(client, message):
    if not chat_autorizado(message.chat.id):
        return
    sucesso = await atualizar_menu_comandos_super(client)
    if sucesso:
        await message.reply_text("✅ Menu do Telegram (botão /) atualizado com todos os comandos!")
    else:
        await message.reply_text("❌ Erro ao atualizar o menu. Veja os logs.")


def _is_admin(message) -> bool:
    """Verifica se quem enviou a mensagem é o admin configurado."""
    user = getattr(message, "from_user", None)
    return bool(user and user.id == ADMIN_ID)


def _atualizar_ytdlp_sync() -> dict:
    """Executa pip install -U yt-dlp em thread e retorna o resultado."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=600,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        atualizado = proc.returncode == 0
        return {"ok": atualizado, "output": output.strip()}
    except Exception as e:
        return {"ok": False, "output": f"Erro ao executar pip: {e}"}


async def _atualizar_ytdlp_async() -> dict:
    """Versão assíncrona (em thread) de atualizar o yt-dlp."""
    return await asyncio.to_thread(_atualizar_ytdlp_sync)


@app.on_message(filters.command("update_ytdlp"))
async def cmd_update_ytdlp(client, message):
    if not chat_autorizado(message.chat.id):
        return
    aviso = await message.reply_text("🔄 Atualizando yt-dlp, aguarde...")
    try:
        res = await _atualizar_ytdlp_async()
        if res["ok"]:
            txt = "✅ **yt-dlp atualizado com sucesso!**"
            if any(txt2 in res["output"] for txt2 in ("already up-to-date", "Satisfied", "Requirement already")):
                txt = "✅ yt-dlp já está na versão mais recente."
            try:
                versao = subprocess.run(
                    [sys.executable, "-m", "yt_dlp", "--version"],
                    capture_output=True, text=True, timeout=30,
                ).stdout.strip()
                if versao:
                    txt += f"\n📦 Versão atual: `{versao}`"
            except Exception:
                pass
        else:
            txt = "❌ **Falha ao atualizar yt-dlp.**\n"
            txt += f"```\n{res['output'][-1000:]}\n```"
        await aviso.edit_text(txt)
    except Exception as e:
        log.error(f"Erro no comando update_ytdlp: {e}")
        await aviso.edit_text(f"❌ Erro inesperado: {str(e)[:200]}")


@app.on_message(filters.command("ig_status"))
async def cmd_ig_status(client, message):
    if not chat_autorizado(message.chat.id):
        return
    try:
        relatorio = await asyncio.to_thread(inspect_cookie_health, COOKIE_PATH)
        validacao = await validate_cookie_health(COOKIE_PATH)
        if validacao.get('valid'):
            relatorio += "\n\n🟢 **Validação real:** sessão CONFIRMADA com o Instagram."
        else:
            relatorio += "\n\n🔴 **Validação real:** ❌ " + (validacao.get('reason') or 'sessão inválida')
        for parte in dividir_texto_longo(relatorio):
            await message.reply_text(parte)
    except Exception as e:
        log.error(f"Erro no comando ig_status: {e}")
        await message.reply_text(f"❌ Erro ao verificar cookies: {str(e)[:200]}")


@app.on_message(filters.command("ig_renew"))
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


async def limpeza_update_ytdlp_periodica():
    """Atualiza o yt-dlp automaticamente a cada 3 dias (de madrugada)."""
    while True:
        await asyncio.sleep(3 * 86400)  # 3 dias
        try:
            hora = datetime.now().hour
            if 3 <= hora <= 5:  # madrugada
                log.info("🔄 Auto-update periódico do yt-dlp...")
                res = await _atualizar_ytdlp_async()
                if res["ok"]:
                    log.info("✅ yt-dlp auto-atualizado (periódico).")
                else:
                    log.warning("⚠️ Auto-update do yt-dlp falhou: %s", res["output"][-300:])
            else:
                log.info("Auto-update do yt-dlp fora da janela de madrugada, adiado.")
        except Exception as e:
            log.error(f"Erro no auto-update periódico do yt-dlp: {e}")


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

@app.on_callback_query(filters.regex(r"^(forcelong|cancellong)_(\d+)"))
async def callback_long_video(client, callback_query):
    action = callback_query.matches[0].group(1)
    msg_id = int(callback_query.matches[0].group(2))
    
    if msg_id not in _retry_cache:
        await callback_query.answer("Essa mensagem já expirou ou foi processada.", show_alert=True)
        try:
            await callback_query.message.edit_text("❌ Ação expirada.")
        except Exception:
            pass
        return
        
    url, usuario_orig, chat_id, original_msg_id = _retry_cache.pop(msg_id)
    
    if action == "cancellong":
        await callback_query.answer("Download cancelado.")
        try:
            await callback_query.message.edit_text(f"🛑 O usuário arregou e cancelou o download do vídeo longo.")
        except Exception:
            pass
        return
        
    if action == "forcelong":
        await callback_query.answer("Forçando download...")
        try:
            msg_espera = await callback_query.message.edit_text("⏳ *Forçando o download do vídeo gigante...*")
        except Exception:
            msg_espera = callback_query.message
            
        try:
            original_message = await client.get_messages(chat_id, original_msg_id)
        except Exception:
            original_message = callback_query.message
            
        await extrair_e_enviar_midia(client, original_message, url, usuario_orig, msg_espera, force_long=True)

async def limpeza_periodica():
    """Remove arquivos órfãos da pasta downloads a cada 30 minutos."""
    while True:
        await asyncio.sleep(1800)  # 30 minutos
        try:
            agora = time.time()
            removidos = 0
            for f in os.listdir(PASTA_DOWNLOADS):
                caminho = PASTA_DOWNLOADS / f
                if caminho.is_file():
                    idade = agora - os.path.getmtime(caminho)
                    if idade > 3600:  # Mais de 1 hora
                        os.remove(caminho)
                        removidos += 1
            if removidos > 0:
                log.info(f"Limpeza periódica: {removidos} arquivos órfãos removidos.")
        except Exception as e:
            log.error(f"Erro na limpeza periódica: {e}")
            
        # Limpa caches em memória
        try:
            agora = time.time()
            # Rate limit cache
            para_deletar = [u for u, ts in _historico_uso.items() if not ts or agora - ts[-1] > RATE_JANELA]
            for u in para_deletar:
                del _historico_uso[u]
                
            # Retry cache
            if len(_retry_cache) > 500:
                _retry_cache.clear()
                
            # Limpa locks de processamento orfãos (se houver algum travado há mais de 10 min)
            # Como o set não guarda o tempo, limpamos tudo se estiver muito grande
            if len(_processing_urls) > 100:
                _processing_urls.clear()
                
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
async def enviar_midia_quote(client, message, qrt_info, match, msg_espera, usuario_orig):
    """Envia a mídia do tweet quoteado como mensagem separada."""
    midias = qrt_info.get('media_extended', [])
    if not midias:
        return

    quote_user = qrt_info.get('user_name', 'Autor')
    quote_text = limpar_texto(qrt_info.get('text', ''))
    legenda_quote = f"📎 Mídia do quote de **{quote_user}**"
    if quote_text:
        legenda_quote += f":\n{quote_text}"
    legenda_quote += f"\n\n👤 Enviado por: {usuario_orig}"

    tem_video = any(m['type'] in ['video', 'gif'] for m in midias)

    if tem_video:
        lista_quote = []
        arquivos_quote = []

        for m in midias:
            if m['type'] not in ['video', 'gif']:
                lista_quote.append(InputMediaPhoto(m['url'], caption=legenda_quote if not lista_quote else ""))
                continue

            duracao_s = m.get('duration_millis', 0) / 1000
            if duracao_s > LIMITE_DURACAO:
                continue

            video_url = m['url']
            log.info(f"X quote: baixando video ({int(duracao_s)}s) via yt-dlp...")
            ydl_opts = {
                'format': _FORMATO_MP4_H264,
                'outtmpl': f"quote_{match.group(2)}_%(index)s.%(ext)s",
                'paths': {'home': str(PASTA_DOWNLOADS)},
                'quiet': True,
                'no_warnings': True,
                'noplaylist': False,
                'match_filter': _filtro_duracao,
                'max_filesize': LIMITE_TAMANHO,
            }
            try:
                info = await _baixar_com_ytdlp(video_url, ydl_opts, msg_espera=msg_espera)
                item = info.get('entries', [info])[0]
                path = None
                if 'requested_downloads' in item:
                    for dl in item['requested_downloads']:
                        if 'filepath' in dl and os.path.exists(dl['filepath']):
                            path = dl['filepath']
                            break
                if not path:
                    path = item.get('filepath')
                    if not path or not os.path.exists(path):
                        path = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(item)
                arquivos_quote.append(path)
                caption_video = legenda_quote if not lista_quote else ""
                lista_quote.append(InputMediaVideo(path, caption=caption_video, supports_streaming=True))
            except Exception as e:
                log.error(f"X quote yt-dlp erro: {e}")
                log.info(f"X quote: tentando download direto: {video_url}")
                try:
                    video_path = str(PASTA_DOWNLOADS / f"x_quote_{match.group(2)}_{int(time.time())}.mp4")
                    dl_session = await get_http_session()
                    await _baixar_url_limitado(
                        dl_session, video_url, video_path, LIMITE_TAMANHO,
                    )
                    arquivos_quote.append(video_path)
                    caption_video = legenda_quote if not lista_quote else ""
                    lista_quote.append(InputMediaVideo(video_path, caption=caption_video, supports_streaming=True))
                except Exception as e2:
                    log.error(f"X quote direct download erro: {e2}")

        if not lista_quote:
            return

        if len(lista_quote) == 1:
            midia = lista_quote[0]
            if isinstance(midia, InputMediaPhoto):
                await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
            else:
                if isinstance(midia.media, str) and midia.media.startswith('http'):
                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                else:
                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
        else:
            for i in range(0, len(lista_quote), 10):
                lote = lista_quote[i:i+10]
                await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                if len(lista_quote) > 10:
                    await asyncio.sleep(2)

        for p in arquivos_quote:
            if os.path.exists(p):
                os.remove(p)
    else:
        lista_quote = []
        for idx, m in enumerate(midias):
            c = legenda_quote if idx == 0 else ""
            lista_quote.append(InputMediaPhoto(m['url'], caption=c))
        await client.send_media_group(message.chat.id, lista_quote[:10], reply_to_message_id=message.id)


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

# -----------------------------------------
# ESCUTA DE MENSAGENS
# -----------------------------------------
COMANDOS = set(command_names(SUPER_COMMANDS))

@app.on_message(filters.text & ~filters.command(list(COMANDOS)))
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
        usuario = "Membro"
        user_id = 0

    # Aceita http://, https://, www. ou até mesmo urls nuas tipo instagram.com/p/...
    url_encontrada = re.search(r'((?:https?://|www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)', texto)
    url_raw = None
    repetido_db = False
    info_db = {}

    if url_encontrada:
        url_raw = url_encontrada.group(1)
        # Se veio sem http, adiciona (o httpx e aiohttp precisam disso)
        if not url_raw.startswith('http'):
            url_raw = 'https://' + url_raw
            
        if not url_permitida(url_raw):
            url_raw = None

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
            _processing_urls.add(url_norm)

    # 1. TWITTER / X
    if url_raw and re.search(r'(x|twitter)\.com', url_raw):
        log.info(f"🐦 Detectado link X: {url_raw}")
        log.info(f"Puxando dados do X: {url_raw}")
        msg_espera = await message.reply_text("🐦 Puxando dados do X...")
        arquivos_x = []
        try:
            match = match_tweet_url(url_raw)
            # Link de perfil (x.com/username) — sem /status/
            if not match and match_profile_url(url_raw):
                try:
                    await msg_espera.delete()
                except Exception:
                    pass
                await responder_perfil_x(client, message, url_raw)
                return
            if match:
                username = match.group(1)
                status_id = match.group(2)
                api_url_vx = build_vxtwitter_url(username, status_id)
                api_url_fx = build_fxtwitter_url(username, status_id)
                headers = {"Accept-Encoding": "gzip, deflate"}
                _x_timeout = aiohttp.ClientTimeout(total=30)

                res = None
                session = await get_http_session()
                try:
                    async with session.get(api_url_vx, headers=headers, timeout=_x_timeout) as resp:
                        if resp.status == 200:
                            res = await resp.json()
                        else:
                            raise ValueError("vxtwitter HTTP status != 200")
                except Exception as e:
                    log.warning(f"vxtwitter falhou para {url_raw} ({e}), tentando fxtwitter...")
                    async with session.get(api_url_fx, headers=headers, timeout=_x_timeout) as resp:
                        if resp.status == 200:
                            res = await resp.json()
                            # fxtwitter returns the tweet wrapped in {"tweet": {...}}
                            if "tweet" in res:
                                res = res["tweet"]
                        else:
                            raise ValueError("fxtwitter HTTP status != 200")
                                
                if not res:
                    raise Exception("Falha ao puxar dados do X (ambas as APIs falharam)")

                texto_base = res.get('text', '')
                
                # --- BUSCA DE QUOTE ANTECIPADA ---
                qrt_info = None
                if 'qrt' in res and res['qrt']:
                    qrt_info = res['qrt']
                    if 'media_extended' not in qrt_info and 'id' in qrt_info:
                        try:
                            qrt_user = qrt_info.get('user_screen_name', 'i')
                            url_qrt_vx = build_vxtwitter_url(qrt_user, qrt_info["id"])
                            url_qrt_fx = build_fxtwitter_url(qrt_user, qrt_info["id"])
                            qrt_data = None
                            try:
                                async with session.get(url_qrt_vx, timeout=_x_timeout) as r:
                                    if r.status == 200:
                                        qrt_data = await r.json()
                                    else:
                                        raise ValueError("QRT vx fail")
                            except Exception:
                                async with session.get(url_qrt_fx, timeout=_x_timeout) as r:
                                    if r.status == 200:
                                        qrt_data = await r.json()
                                        if "tweet" in qrt_data:
                                            qrt_data = qrt_data["tweet"]

                            if qrt_data:
                                if 'media_extended' in qrt_data and qrt_data['media_extended']:
                                    qrt_info['media_extended'] = qrt_data['media_extended']
                                if 'text' in qrt_data and not qrt_info.get('text'):
                                    qrt_info['text'] = qrt_data['text']
                        except Exception as e:
                            log.error(f"Erro ao buscar quote antecipado: {e}")

                tem_midia_no_quote = qrt_info and 'media_extended' in qrt_info and len(qrt_info['media_extended']) > 0
                
                if qrt_info:
                    if not tem_midia_no_quote and 'text' in qrt_info:
                        texto_base += f"\n\n🔁 [Quote - {qrt_info.get('user_name', 'Autor')}]:\n{qrt_info['text']}"
                    elif tem_midia_no_quote:
                        texto_base += f"\n\n🔁 [Quote de {qrt_info.get('user_name', 'Autor')} logo abaixo 👇]"
                    
                # Tradução automática do texto do tweet (e quote) para PT, se não estiver em português.
                # Se traduzir, inclui o texto original + aviso do idioma de origem.
                detalhes_trad = await asyncio.to_thread(traduzir_com_detalhes, texto_base)
                if detalhes_trad["foi_traduzido"]:
                    cap_limpa = (
                        f"{detalhes_trad['traduzido']}\n\n"
                        f"---\n"
                        f"🔎 Traduzido do {nome_idioma(detalhes_trad['idioma_origem'])}:\n\n"
                        f"{limpar_texto(detalhes_trad['original'])}"
                    )
                else:
                    cap_limpa = limpar_texto(detalhes_trad["original"])
                legenda = montar_legenda(cap_limpa, res.get('user_name', 'Autor'), usuario, emoji="📸")

                if 'media_extended' in res and len(res['media_extended']) > 0:
                    tem_video = any(m['type'] in ['video', 'gif'] for m in res['media_extended'])

                    if tem_video:
                        lista_telegram = []
                        arquivos_x_para_enviar = []

                        for m in res['media_extended']:
                            if m['type'] not in ['video', 'gif']:
                                lista_telegram.append(InputMediaPhoto(m['url'], caption=legenda if not lista_telegram else ""))
                                continue

                            duracao_s = m.get('duration_millis', 0) / 1000
                            video_url = m['url']

                            if duracao_s > LIMITE_DURACAO:
                                await msg_espera.edit_text(erro_aleatorio(ERROS_VIDEO_LONGO, min=LIMITE_DURACAO // 60))
                                return

                            log.info(f"X: baixando video ({int(duracao_s)}s) via yt-dlp...")
                            ydl_opts = {
                                'format': _FORMATO_MP4_H264,
                                'outtmpl': f"{match.group(2)}_%(index)s.%(ext)s",
                                'paths': {'home': str(PASTA_DOWNLOADS)},
                                'quiet': True,
                                'no_warnings': True,
                                'noplaylist': False,
                                'match_filter': _filtro_duracao,
                                'max_filesize': LIMITE_TAMANHO,
                            }
                            try:
                                info = await _baixar_com_ytdlp(url_raw, ydl_opts, msg_espera=msg_espera)

                                item = info.get('entries', [info])[0]
                                path = None
                                if 'requested_downloads' in item:
                                    for dl in item['requested_downloads']:
                                        if 'filepath' in dl and os.path.exists(dl['filepath']):
                                            path = dl['filepath']
                                            break
                                if not path:
                                    path = item.get('filepath')
                                    if not path or not os.path.exists(path):
                                        path = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(item)
                                        if not os.path.exists(path):
                                            raise Exception("Arquivo nao encontrado apos download.")

                                arquivos_x.append(path)
                                caption_video = legenda if not lista_telegram else ""
                                lista_telegram.append(InputMediaVideo(path, caption=caption_video, supports_streaming=True))
                            except Exception as e:
                                log.error(f"X yt-dlp erro: {e}")
                                log.info(f"X: tentando download direto: {video_url}")
                                try:
                                    video_path = str(PASTA_DOWNLOADS / f"x_{match.group(2)}_{int(time.time())}.mp4")
                                    dl_session = await get_http_session()
                                    await _baixar_url_limitado(
                                        dl_session, video_url, video_path, LIMITE_TAMANHO,
                                    )
                                    arquivos_x.append(video_path)
                                    caption_video = legenda if not lista_telegram else ""
                                    lista_telegram.append(InputMediaVideo(video_path, caption=caption_video, supports_streaming=True))
                                except Exception as e2:
                                    log.error(f"X direct download erro: {e2}")
                                    raise

                        if not lista_telegram:
                            raise Exception("Nenhuma midia encontrada.")

                        if len(lista_telegram) == 1:
                            midia = lista_telegram[0]
                            if isinstance(midia, InputMediaPhoto):
                                await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
                            else:
                                if isinstance(midia.media, str) and midia.media.startswith('http'):
                                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                                else:
                                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id, progress=_progresso_upload(msg_espera))
                        else:
                            for i in range(0, len(lista_telegram), 10):
                                lote = lista_telegram[i:i+10]
                                await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                                if len(lista_telegram) > 10:
                                    await asyncio.sleep(2)

                        async with DOWNLOAD_COUNT_LOCK:
                            DOWNLOAD_COUNT += 1
                        log.info(f"Sucesso X: {url_raw} ({len(lista_telegram)} itens)")
                    else:
                        lista = []
                        for idx, m in enumerate(res['media_extended']):
                            c = legenda if idx == 0 else ""
                            lista.append(InputMediaPhoto(m['url'], caption=c))

                        await client.send_media_group(message.chat.id, lista[:10], reply_to_message_id=message.id)
                        async with DOWNLOAD_COUNT_LOCK:
                            DOWNLOAD_COUNT += 1
                        log.info(f"Sucesso X (fotos): {url_raw}")
                    try:
                        await msg_espera.delete()
                    except Exception:
                        pass
                else:
                    log.info(f"X: tweet sem midia, enviando texto...")
                    msg = f"📝 {res.get('user_name', 'Autor')}:\n{cap_limpa}\n\n👤 Enviado por: {usuario}"
                    for parte in dividir_texto_longo(msg):
                        await message.reply_text(parte)
                    try:
                        await msg_espera.delete()
                    except Exception:
                        pass
                    log.info(f"Sucesso X (texto): {url_raw}")

                # Se o tweet quoteado tiver mídia, envia separado
                if tem_midia_no_quote:
                    await enviar_midia_quote(client, message, qrt_info, match, msg_espera, usuario)

                # Registra link e verifica duplicata SOMENTE após sucesso
                repetido_db, info_db = await asyncio.to_thread(db.registrar_link_e_checar, url_norm, message.chat.id, message.from_user.first_name or "Membro", user_id)
                if repetido_db:
                    await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        except Exception as e:
            log.error(f"Erro X: {e}")
            await msg_espera.edit_text(erro_aleatorio(ERROS_X))
            _retry_cache[msg_espera.id] = (url_raw, usuario, message.chat.id, message.id)
        finally:
            for p in arquivos_x:
                if os.path.exists(p):
                    os.remove(p)
            async with _processing_lock:
                _processing_urls.discard(url_norm)
        return

    # 2. INSTAGRAM (handler dedicado)
    if url_raw and any(d in url_raw for d in ["instagram.com", "instagr.am"]):
        # Detecta link de perfil do Instagram (não post/reel/stories)
        ig_path = urlparse(url_raw).path.strip('/')
        ig_parts = [p for p in ig_path.split('/') if p]
        ig_known_types = {'p', 'reel', 'reels', 'tv', 'ad', 'stories'}

        if ig_parts and get_profile_username(url_raw):
            await responder_perfil_instagram(client, message, url_raw)
            async with _processing_lock:
                _processing_urls.discard(url_norm)
            return

        # Aceita se qualquer segmento do path é um tipo conhecido (ex: /username/reel/SHORTCODE/)
        # ou se o shortcode regex encontra um match (cobertura extra para formatos novos)
        has_known_type = any(part in ig_known_types for part in ig_parts)
        has_shortcode = bool(re.search(r'/(?:p|reel|reels|ad|tv)/[A-Za-z0-9_-]+', ig_path))
        has_story = bool(re.search(r'/stories/[^/]+/[0-9]+', ig_path))

        if ig_parts and not has_known_type and not has_shortcode and not has_story:
            log.info("Instagram link não reconhecido: %s (parts=%s)", url_raw, ig_parts)
            await message.reply_text("❌ Link do Instagram não reconhecido.\nEnvie um perfil, post, Reels ou Stories específico.")
            async with _processing_lock:
                _processing_urls.discard(url_norm)
            return

        agora_ts = time.time()
        if url_norm in _failed_url_cache and agora_ts - _failed_url_cache[url_norm] < 300:
            tr = int(300 - (agora_ts - _failed_url_cache[url_norm]))
            tempo_str = f"{tr // 60}min {tr % 60}s"
            await message.reply_text(erro_aleatorio(ERROS_COOLDOWN, tempo=tempo_str))
            async with _processing_lock:
                _processing_urls.discard(url_norm)
            return
            
        msg_espera = await message.reply_text("⏳ *Baixando do Instagram...*")
        sucesso = await processar_instagram(client, message, url_raw, usuario, msg_espera)

        if sucesso:
            _failed_url_cache.pop(url_norm, None)
            repetido_db, info_db = await asyncio.to_thread(db.registrar_link_e_checar, url_norm, message.chat.id, message.from_user.first_name or "Membro", user_id)
            if repetido_db:
                await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        else:
            _failed_url_cache[url_norm] = agora_ts
            
        async with _processing_lock:
            _processing_urls.discard(url_norm)
        return

    # 3. YOUTUBE, TIKTOK, THREADS, PINTEREST (yt-dlp generico)
    if url_raw and any(d in url_raw for d in ["youtube.com", "youtu.be", "tiktok.com", "threads.net", "pinterest.com", "pin.it"]):
        url = url_raw
        # Suporte para shorts e links normais com limpeza de tracking
        yt_match = re.search(r'(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]+)', url)
        if yt_match:
            url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        elif not any(d in url for d in ["youtube.com", "youtu.be", "google.com"]):
            url = urlunparse(urlparse(url)._replace(query="")).rstrip("/")

        msg_espera = await message.reply_text("⏳ *Puxando mídia original...*")
        sucesso = await extrair_e_enviar_midia(client, message, url, usuario, msg_espera)

        if sucesso and not any(d in url_raw for d in ["youtube.com", "youtu.be"]):
            repetido_db, info_db = await asyncio.to_thread(db.registrar_link_e_checar, url_norm, message.chat.id, message.from_user.first_name or "Membro", user_id)
            if repetido_db:
                await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        async with _processing_lock:
            _processing_urls.discard(url_norm)
        return


    # 4. OUTROS LINKS (fallback)
    if url_raw and any(d in url_raw for d in DOMINIOS_PERMITIDOS):
        if not url_permitida(url_raw):
            return

        url = url_raw
        yt_match = re.search(r'(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]+)', url)
        if yt_match:
            url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        elif not any(d in url for d in ["youtube.com", "youtu.be", "google.com"]):
            url = urlunparse(urlparse(url)._replace(query="")).rstrip("/")

        url_curta = await encurtar_url(url) if len(url) > 60 else url

        msg_espera = await message.reply_text("⚙️ Processando...")
        sucesso = await extrair_e_enviar_midia(client, message, url, usuario, msg_espera)

        if sucesso and not any(d in url_raw for d in ["youtube.com", "youtu.be"]):
            repetido_db, info_db = await asyncio.to_thread(db.registrar_link_e_checar, url_norm, message.chat.id, message.from_user.first_name or "Membro", user_id)
            if repetido_db:
                await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        
        async with _processing_lock:
            _processing_urls.discard(url_norm)

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
        asyncio.get_event_loop().create_task(_canario_conectividade())
        try:
            await idle()
        finally:
            await app.stop()

    try:
        asyncio.get_event_loop().run_until_complete(_rodar_with_canario())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error(f"Erro fatal na execução do bot: {e}", exc_info=True)
        os._exit(1)
    finally:
        try:
            asyncio.get_event_loop().run_until_complete(close_http_session())
        except Exception:
            pass
