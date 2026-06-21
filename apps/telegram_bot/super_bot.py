# Teste do sistema de atualização
import sys
import os
import re
import json
import time
import random
import asyncio
import logging
import psutil
from datetime import datetime, timedelta
from functools import partial
from urllib.parse import urlparse, urlunparse
from collections import defaultdict
from logging.handlers import RotatingFileHandler
import yt_dlp
import aiohttp

from pyrogram import Client, filters, raw
from pyrogram.types import InputMediaPhoto, InputMediaVideo
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
from apps.telegram_bot.instagram_extractor import download_instagram

load_dotenv()
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
PASTA_DOWNLOADS = Path(RAIZ) / "downloads"
COOKIE_PATH = os.path.join(RAIZ, "data", "instagram_cookies.txt")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODO_ZUEIRA = os.getenv("MODO_ZUEIRA", "1") == "1"
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

_grupos_raw = os.getenv("GRUPOS_AUTORIZADOS", "")
GRUPOS_AUTORIZADOS = [int(g.strip()) for g in _grupos_raw.split(",") if g.strip()]

DOMINIOS_PERMITIDOS = [
    "x.com", "twitter.com", "youtube.com", "youtu.be",
    "instagram.com", "instagr.am", "tiktok.com", "threads.net",
    "pinterest.com", "pin.it"
]

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
LOG_DIR = os.path.join(RAIZ, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    maxBytes=5*1024*1024,
    backupCount=3,
    encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[log_handler, logging.StreamHandler()]
)

logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("yt_dlp").setLevel(logging.ERROR)

log = logging.getLogger("SuperBot")

# -----------------------------------------
# CLIENTE
# -----------------------------------------
app = Client("meu_super_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
def _detectar_extensao(url: str, content_type: str = '') -> str:
    """Detecta extensão de arquivo a partir do Content-Type e/ou URL da CDN do Instagram.
    
    Instagram CDN URLs têm dots no path (ex: t51.2885-15) que confundem split('.'),
    então priorizamos o Content-Type header e validamos contra extensões conhecidas.
    """
    # 1. Content-Type header (mais confiável)
    if content_type:
        ct = content_type.lower().split(';')[0].strip()
        ct_map = {
            'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
            'image/webp': 'webp', 'image/gif': 'gif', 'image/heic': 'heic',
            'image/heif': 'heif', 'video/mp4': 'mp4', 'video/quicktime': 'mov',
            'video/webm': 'webm',
        }
        if ct in ct_map:
            return ct_map[ct]

    # 2. Extensão do último segmento da URL (apenas o filename, não o path todo)
    extensoes_validas = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4', 'mov', 'm4v', 'webm', 'heic'}
    try:
        path_sem_query = url.split('?')[0]
        ultimo_segmento = path_sem_query.rsplit('/', 1)[-1]  # pega só o filename
        if '.' in ultimo_segmento:
            ext = ultimo_segmento.rsplit('.', 1)[-1].lower()
            if ext in extensoes_validas:
                return ext
    except Exception:
        pass

    # 3. Fallback: jpg para imagens (caso mais comum no Instagram)
    return 'jpg'


def limpar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r'#\w+', '', texto)
    texto = re.sub(r'\n\s*\n', '\n\n', texto)
    return texto.strip()

def montar_legenda(texto_base: str, autor: str, usuario: str, emoji: str = "✨", limite: int = 1024) -> str:
    """Monta legenda respeitando o limite de caracteres do Telegram (1024 para captions)."""
    sufixo = f"\n\nAutor: {autor}\n👤 Enviado por: {usuario}"
    espaco_disponivel = limite - len(sufixo) - len(emoji) - 5  # 5 = espaço + "..." + margem
    if espaco_disponivel < 50:
        espaco_disponivel = 50
    if len(texto_base) > espaco_disponivel:
        texto_base = texto_base[:espaco_disponivel] + "..."
    return f"{emoji} {texto_base}{sufixo}"

def dividir_texto_longo(texto: str, limite: int = 4096) -> list[str]:
    """Divide texto longo em múltiplas mensagens respeitando o limite do Telegram."""
    if len(texto) <= limite:
        return [texto]
    partes = []
    while texto:
        if len(texto) <= limite:
            partes.append(texto)
            break
        corte = texto.rfind('\n', 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = texto.rfind(' ', 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = limite
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip()
    return partes

def _progresso_upload(msg_espera):
    """Cria um callback de progresso para upload de vídeo."""
    estado = {"ultimo_pct": 0, "ultimo_tempo": 0}
    
    async def _callback(current, total):
        if total == 0:
            return
        pct = int(current * 100 / total)
        agora = time.time()
        
        # Atualiza a cada 15% E no mínimo a cada 2s para evitar FloodWait
        if (pct - estado["ultimo_pct"] >= 15 and agora - estado["ultimo_tempo"] > 2.0) or pct == 100:
            if pct == 100 and estado["ultimo_pct"] == 100:
                return
            estado["ultimo_pct"] = pct
            estado["ultimo_tempo"] = agora
            barra = "█" * (pct // 10) + "░" * (10 - pct // 10)
            try:
                await msg_espera.edit_text(f"📤 Enviando... {barra} {pct}%")
            except Exception:
                pass
    return _callback

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

def normalizar_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        tweet_match = re.search(r'status/(\d+)', url)
        if tweet_match:
            return f"tweet:{tweet_match.group(1)}"
        limpo = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        return limpo.rstrip('/').lower()
    except Exception:
        return url.lower().strip()

async def encurtar_url(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://is.gd/create.php?format=simple&url={url}", timeout=5) as r:
                if r.status == 200:
                    return await r.text()
    except Exception:
        pass
    return url

# -----------------------------------------
# MOTOR DE DOWNLOAD
# -----------------------------------------
def _filtro_duracao(info_dict, *, incomplete):
    duracao = info_dict.get('duration')
    if duracao and duracao > LIMITE_DURACAO:
        return f"Video tem {duracao}s, acima do limite de {LIMITE_DURACAO}s"

def _processar_com_ytdlp(url, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)

async def extrair_e_enviar_midia(client, message, url, usuario, msg_espera):
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
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': str(PASTA_DOWNLOADS / '%(id)s_%(index)s.%(ext)s'),
                    'paths': {'home': str(PASTA_DOWNLOADS)},
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': False,
                    'match_filter': _filtro_duracao,
                    'max_filesize': LIMITE_TAMANHO,
                }

                if any(d in url for d in ["instagram.com", "instagr.am", "threads.net"]):
                    ydl_opts['http_headers'] = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                        'Referer': 'https://www.instagram.com/',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }

                loop = asyncio.get_running_loop()
                info = await loop.run_in_executor(None, partial(_processar_com_ytdlp, url, ydl_opts))

                midias = info.get('entries', [info])
                lista_telegram = []

                legenda_base = limpar_texto(info.get('title') or info.get('description') or "")
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
                            # Verifica se foi filtrado por tamanho antes de dar erro
                            filesize = item.get('filesize') or item.get('filesize_approx')
                            if filesize and filesize > LIMITE_TAMANHO:
                                raise Exception(f"Arquivo muito grande ({filesize / 1024 / 1024:.1f}MB). O limite é de 50MB.")
                            continue

                    arquivos_para_deletar.append(path)
                    ext = path.lower().split('.')[-1]
                    cap = legenda_final if i == 0 else ""

                    if ext in ['jpg', 'jpeg', 'png', 'webp']:
                        lista_telegram.append(InputMediaPhoto(path, caption=cap))
                    else:
                        lista_telegram.append(InputMediaVideo(path, caption=cap, supports_streaming=True))

                if not lista_telegram:
                    raise Exception("Nenhum arquivo valido encontrado.")

                if len(lista_telegram) == 1:
                    midia = lista_telegram[0]
                    if isinstance(midia, InputMediaPhoto):
                        await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
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
                    erro_msg = await msg_espera.edit_text(erro_aleatorio(ERROS_VIDEO_LONGO, min=LIMITE_DURACAO // 60))
                    _retry_cache[msg_espera.id] = (url, usuario, message.chat.id, message.id)
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
                    await msg_espera.edit_text(erro_aleatorio(ERROS_INSTAGRAM))
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
                async with aiohttp.ClientSession(headers=cdn_headers) as session:
                    for i, m_url in enumerate(midias_urls):
                        try:
                            async with session.get(m_url) as response:
                                if response.status == 200:
                                    content_type = response.headers.get('Content-Type', '')
                                    ext = _detectar_extensao(m_url, content_type)
                                    data = await response.read()
                                    
                                    # Converte webp/heic → jpg (Telegram rejeita esses formatos como foto)
                                    if ext in ('webp', 'heic', 'heif'):
                                        try:
                                            from PIL import Image
                                            import io
                                            img = Image.open(io.BytesIO(data))
                                            buf = io.BytesIO()
                                            img.convert('RGB').save(buf, format='JPEG', quality=95)
                                            data = buf.getvalue()
                                            ext = 'jpg'
                                            log.info(f"   Convertido {ext} → jpg para compatibilidade Telegram")
                                        except Exception as conv_err:
                                            log.warning(f"Falha ao converter {ext} para jpg: {conv_err}")
                                            ext = 'jpg'  # Tenta enviar mesmo assim
                                    
                                    caminho_temp = PASTA_DOWNLOADS / f"temp_insta_{message.id}_{i}.{ext}"
                                    
                                    with open(caminho_temp, 'wb') as f:
                                        f.write(data)
                                    
                                    arquivos_para_deletar.append(str(caminho_temp))
                                    cap = legenda_final if i == 0 else ""
                                    is_video = ext in ('mp4', 'mov', 'm4v', 'webm')

                                    if is_video:
                                        lista_telegram.append(InputMediaVideo(str(caminho_temp), caption=cap, supports_streaming=True))
                                    else:
                                        lista_telegram.append(InputMediaPhoto(str(caminho_temp), caption=cap))
                                    log.info(f"   Mídia {i+1}/{len(midias_urls)} baixada: ext={ext}, size={len(data)} bytes")
                                else:
                                    log.warning(f"Falha ao baixar URL do Instagram ({response.status}): {m_url[:100]}")
                        except Exception as e:
                            log.error(f"Erro ao baixar midia individual do Instagram: {e}")

            # Envio parcial: envia o que conseguiu, mesmo se nem tudo foi baixado
            if lista_telegram:
                if len(lista_telegram) == 1:
                    midia = lista_telegram[0]
                    if isinstance(midia, InputMediaPhoto):
                        await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
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
                # Detecta cookies expirados
                if "login" in str(e).lower() or "cookie" in str(e).lower():
                    await avisar_admin_cookies(client, "expirados ou inválidos")
                try:
                    await msg_espera.edit_text(erro_aleatorio(ERROS_INSTAGRAM))
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

@app.on_message(filters.command("help"))
async def cmd_help(client, message):
    if not chat_autorizado(message.chat.id):
        return
    txt = (
        "**🤖 Guia do Super Bot**\n\n"
        "**📊 Rankings**\n"
        "- `/ranking` - Ver o ranking dos últimos 7 dias.\n"
        "- `/bocadeleite` - Ver o pódio do mês atual.\n"
        "- `/anual` - Ver o Hall da Fama do ano.\n\n"
        "**🎯 Castigo**\n"
        "- `/repetido` - (Em resposta a alguém) Aplica o castigo manual.\n\n"
        "**😈 Diversão**\n"
        "- `/comi` - Escolhe uma vítima aleatória do grupo.\n\n"
        "**🔧 Utilidades**\n"
        "- `/id` - Mostra o ID deste chat.\n"
        "- `/stats` - Status técnico do bot.\n"
        "- `/help` - Mostra esta mensagem."
    )
    await message.reply_text(txt)

@app.on_message(filters.command("repetido"))
async def cmd_repetido_manual(client, message):
    if not chat_autorizado(message.chat.id):
        return
    if not message.reply_to_message:
        return await message.reply_text("💡 Dica: Use este comando em resposta a alguém que postou repetido!")

    target = message.reply_to_message
    mencao = target.from_user.mention
    txt = f"**🚨 BOCA DE LEITE {mencao}! (Castigo Manual)**"

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
    if not chat_autorizado(message.chat.id):
        return
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
        from pyrogram.types import BotCommand
        lista_comandos = [
            BotCommand("help", "📖 Guia do Super Bot"),
            BotCommand("ranking", "📊 Ranking semanal de vacilos"),
            BotCommand("bocadeleite", "🏆 Pódio do mês atual"),
            BotCommand("anual", "👑 Hall da Fama do ano"),
            BotCommand("repetido", "🎯 Castigo manual (responda a alguém)"),
            BotCommand("comi", "😈 Escolhe uma vítima aleatória"),
            BotCommand("bloq", "🚫 Bloqueia alguém de mandar link"),
            BotCommand("id", "🆔 Mostra o ID deste chat"),
            BotCommand("stats", "📊 Status técnico do bot"),
            BotCommand("ping", "🏓 Verifica se o bot está online"),
            BotCommand("retry", "🔄 Tenta baixar novamente (responda ao erro)"),
            BotCommand("sync", "🔄 Sincroniza o menu de comandos"),
        ]
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
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(PASTA_DOWNLOADS / f"quote_{match.group(2)}_%(index)s.%(ext)s"),
                'paths': {'home': str(PASTA_DOWNLOADS)},
                'quiet': True,
                'no_warnings': True,
                'noplaylist': False,
                'match_filter': _filtro_duracao,
                'max_filesize': LIMITE_TAMANHO,
            }
            try:
                loop = asyncio.get_running_loop()
                info = await loop.run_in_executor(None, partial(_processar_com_ytdlp, video_url, ydl_opts))
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
                    async with aiohttp.ClientSession() as dl_session:
                        async with dl_session.get(video_url, timeout=60) as dl_resp:
                            if dl_resp.status == 200:
                                with open(video_path, 'wb') as vf:
                                    vf.write(await dl_resp.read())
                                arquivos_quote.append(video_path)
                                caption_video = legenda_quote if not lista_quote else ""
                                lista_quote.append(InputMediaVideo(video_path, caption=caption_video, supports_streaming=True))
                            else:
                                raise Exception("Download direto falhou")
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

    texto = f"🚨 BOCA DE LEITE {quem_ago}! Esse link já foi enviado {vezes} vezes no grupo (primeiro por {quem_mandou_primeiro}). Presta atenção no grupo!"

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
COMANDOS = {"ranking", "bocadeleite", "anual", "stats", "help", "repetido", "id", "comi", "ping", "retry", "bloq", "sync"}

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
        url_norm = urlunparse(urlparse(url_raw)._replace(query="")).lower().rstrip("/")
        
        # Normalização específica para Twitter/X (ignora nome de usuário para evitar falsos negativos)
        tw_match = re.search(r'(?:x|twitter)\.com/[^/]+/status/(\d+)', url_norm)
        if tw_match:
            url_norm = f"https://x.com/i/status/{tw_match.group(1)}"

        repetido_db, info_db = db.checar_link(url_norm)

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
            match = re.search(r'(?:x|twitter)\.com/([^/]+)/status/(\d+)', url_raw)
            if match:
                api_url = f"https://api.vxtwitter.com/{match.group(1)}/status/{match.group(2)}"
                headers = {"Accept-Encoding": "gzip, deflate"}
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), headers=headers) as session:
                    async with session.get(api_url) as resp:
                        res = await resp.json()

                texto_base = res.get('text', '')
                if 'qrt' in res and res['qrt'] and 'text' in res['qrt']:
                    texto_base += f"\n\n🔁 [Quote - {res['qrt'].get('user_name', 'Autor')}]:\n{res['qrt']['text']}"
                cap_limpa = limpar_texto(texto_base)
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
                                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                                'outtmpl': str(PASTA_DOWNLOADS / f"{match.group(2)}_%(index)s.%(ext)s"),
                                'paths': {'home': str(PASTA_DOWNLOADS)},
                                'quiet': True,
                                'no_warnings': True,
                                'noplaylist': False,
                                'match_filter': _filtro_duracao,
                                'max_filesize': LIMITE_TAMANHO,
                            }
                            try:
                                loop = asyncio.get_running_loop()
                                info = await loop.run_in_executor(None, partial(_processar_com_ytdlp, url_raw, ydl_opts))

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
                                    async with aiohttp.ClientSession() as dl_session:
                                        async with dl_session.get(video_url, timeout=60) as dl_resp:
                                            if dl_resp.status == 200:
                                                with open(video_path, 'wb') as vf: vf.write(await dl_resp.read())
                                                arquivos_x.append(video_path)
                                                caption_video = legenda if not lista_telegram else ""
                                                lista_telegram.append(InputMediaVideo(video_path, caption=caption_video, supports_streaming=True))
                                            else: raise Exception("Download direto falhou")
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
                if 'qrt' in res and res['qrt']:
                    qrt_id = res['qrt'].get('id')
                    qrt_media = res['qrt'].get('media_extended')
                    if qrt_media:
                        await enviar_midia_quote(client, message, res['qrt'], match, msg_espera, usuario)
                    elif qrt_id:
                        try:
                            qrt_user = res['qrt'].get('user_screen_name', 'i')
                            url_qrt = f"https://api.vxtwitter.com/{qrt_user}/status/{qrt_id}"
                            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                                async with s.get(url_qrt) as r:
                                    qrt_data = await r.json()
                            if 'media_extended' in qrt_data and qrt_data['media_extended']:
                                qrt_info = {
                                    'media_extended': qrt_data['media_extended'],
                                    'user_name': qrt_data.get('user_name', res['qrt'].get('user_name', 'Autor')),
                                    'text': qrt_data.get('text', '')
                                }
                                await enviar_midia_quote(client, message, qrt_info, match, msg_espera, usuario)
                        except Exception as e:
                            log.error(f"Erro ao buscar quote: {e}")

                # Registra link e verifica duplicata SOMENTE após sucesso
                repetido_db, info_db = db.registrar_link_e_checar(url_norm, message.from_user.first_name or "Membro", user_id)
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
            repetido_db, info_db = db.registrar_link_e_checar(url_norm, message.from_user.first_name or "Membro", user_id)
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
            repetido_db, info_db = db.registrar_link_e_checar(url_norm, message.from_user.first_name or "Membro", user_id)
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
            repetido_db, info_db = db.registrar_link_e_checar(url_norm, message.from_user.first_name or "Membro", user_id)
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

        enviados = 0
        for grupo_id in GRUPOS_AUTORIZADOS:
            try:
                await app.send_message(grupo_id, txt)
                enviados += 1
            except Exception as e:
                log.error(f"Erro ao enviar notificação de update para {grupo_id}: {e}")

        # Fallback: se não há grupos autorizados, envia para o admin
        if not GRUPOS_AUTORIZADOS and ADMIN_ID:
            try:
                await app.send_message(ADMIN_ID, txt)
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
    asyncio.get_event_loop().create_task(limpeza_periodica())
    asyncio.get_event_loop().create_task(notificar_atualizacao())
    app.run()
