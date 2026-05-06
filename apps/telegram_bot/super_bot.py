import sys
import os
import re
import os
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

# Fix the import and RAÍZ problem:
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
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

LIMITE_DURACAO = 300
LIMITE_TAMANHO = 50_000_000
MAX_DOWNLOADS = 3
RATE_LIMIT = 10
RATE_JANELA = 60

AUDIO_BOCA_LEITE_DIR = os.path.join(RAIZ, "assets", "audios")
PASTA_DOWNLOADS = Path(RAIZ) / "downloads"
COOKIE_PATH = os.path.join(RAIZ, "data", "instagram_cookies.txt")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODO_ZUEIRA = os.getenv("MODO_ZUEIRA", "1") == "1"

_grupos_raw = os.getenv("GRUPOS_AUTORIZADOS", "")
GRUPOS_AUTORIZADOS = [int(g.strip()) for g in _grupos_raw.split(",") if g.strip()]

DOMINIOS_PERMITIDOS = [
    "x.com", "twitter.com", "youtube.com", "youtu.be",
    "instagram.com", "instagr.am", "tiktok.com", "threads.net",
    "pinterest.com", "pin.it"
]

PACKS = {"repetido": "POSTREPETIDO", "meus": "Meus325", "monkes": "Monkes"}

semaforo = asyncio.Semaphore(MAX_DOWNLOADS)
_historico_uso = defaultdict(list)

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
def limpar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r'#\w+', '', texto)
    texto = re.sub(r'\n\s*\n', '\n\n', texto)
    return texto.strip()

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
    global DOWNLOAD_COUNT
    arquivos_para_deletar = []
    async with semaforo:
        try:
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
            if len(legenda_base) > 800:
                legenda_base = legenda_base[:800] + "..."
            autor = info.get('uploader') or info.get('channel') or "Autor"
            legenda_final = f"✨ {legenda_base}\n\nAutor: {autor}\n👤 Enviado por: {usuario}"

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
                        path = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(item)
                        if not os.path.exists(path):
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
                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id)
            else:
                for i in range(0, len(lista_telegram), 10):
                    lote = lista_telegram[i:i+10]
                    await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                    if len(lista_telegram) > 10:
                        await asyncio.sleep(2)

            async with DOWNLOAD_COUNT_LOCK:
                DOWNLOAD_COUNT += 1
            log.info(f"Sucesso: {url} ({len(lista_telegram)} itens)")

        except yt_dlp.utils.DownloadError as e:
            erro_str = str(e)
            if "Video tem" in erro_str:
                await msg_espera.edit_text("🚫 Vídeo Extenso! O limite é de 5 minutos.")
            elif "File is larger" in erro_str:
                await msg_espera.edit_text("📦 Arquivo muito grande! O limite é de 50MB.")
            else:
                log.error(f"Erro yt-dlp: {e}")
                try:
                    await msg_espera.edit_text("❌ Falha na extração. Post privado ou indisponível.")
                except Exception:
                    pass
            await asyncio.sleep(6)
        except Exception as e:
            log.error(f"Erro Motor: {e}")
            try:
                await msg_espera.edit_text("💥 Erro inesperado ao processar mídia.")
            except Exception:
                pass
            await asyncio.sleep(5)
        finally:
            try:
                await msg_espera.delete()
            except Exception:
                pass
            for p in arquivos_para_deletar:
                if os.path.exists(p):
                    os.remove(p)

# -----------------------------------------
# INSTAGRAM HANDLER
# -----------------------------------------
async def processar_instagram(client, message, url, usuario, msg_espera, link_duplicado=None):
    """Handler dedicado para Instagram com cookies + embed fallback."""
    arquivos_para_deletar = []
    try:
        result = await download_instagram(url, COOKIE_PATH, str(PASTA_DOWNLOADS))

        if not result:
            await msg_espera.edit_text("📸 Não foi possível baixar do Instagram. O post pode ser privado ou estar indisponível.")
            await asyncio.sleep(6)
            try:
                await msg_espera.delete()
            except Exception:
                pass
            return

        legenda_base = limpar_texto(result.get('title', ''))
        if len(legenda_base) > 800:
            legenda_base = legenda_base[:800] + "..."
        autor = result.get('uploader', 'Autor')
        legenda_final = f"📸 {legenda_base}\n\nAutor: {autor}\n👤 Enviado por: {usuario}"
        
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
            
            async with aiohttp.ClientSession() as session:
                for i, m_url in enumerate(midias_urls):
                    try:
                        async with session.get(m_url) as response:
                            if response.status == 200:
                                ext = m_url.split('?')[0].split('.')[-1].lower() if '.' in m_url.split('?')[0] else 'mp4'
                                caminho_temp = PASTA_DOWNLOADS / f"temp_insta_{message.id}_{i}.{ext}"
                                
                                with open(caminho_temp, 'wb') as f:
                                    f.write(await response.read())
                                
                                arquivos_para_deletar.append(str(caminho_temp))
                                cap = legenda_final if i == 0 else ""
                                is_video = any(v in ext for v in ['mp4', 'mov', 'm4v'])

                                if is_video:
                                    lista_telegram.append(InputMediaVideo(str(caminho_temp), caption=cap, supports_streaming=True))
                                else:
                                    lista_telegram.append(InputMediaPhoto(str(caminho_temp), caption=cap))
                            else:
                                log.warning(f"Falha ao baixar URL do Instagram ({response.status}): {m_url}")
                    except Exception as e:
                        log.error(f"Erro ao baixar midia individual do Instagram: {e}")

        if not lista_telegram:
            raise Exception("Nenhum arquivo válido encontrado ou baixado.")

        if len(lista_telegram) == 1:
            midia = lista_telegram[0]
            if isinstance(midia, InputMediaPhoto):
                await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
            else:
                await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id)
        else:
            for i in range(0, len(lista_telegram), 10):
                lote = lista_telegram[i:i+10]
                await client.send_media_group(message.chat.id, lote, reply_to_message_id=message.id)
                if len(lista_telegram) > 10:
                    await asyncio.sleep(2)

        async with DOWNLOAD_COUNT_LOCK:
            DOWNLOAD_COUNT += 1
        log.info(f"Instagram sucesso (upload): {url} ({len(lista_telegram)} itens)")
        await msg_espera.delete()

    except Exception as e:
        log.error(f"Erro Instagram handler: {e}")
        try:
            await msg_espera.edit_text("⚠️ Erro ao processar Instagram. Tente novamente mais tarde.")
            await asyncio.sleep(5)
            await msg_espera.delete()
        except Exception:
            pass
    finally:
        for p in arquivos_para_deletar:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    log.error(f"Erro ao deletar arquivo temporário {p}: {e}")

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
        "- `/comi` - Escolhe uma vítima aleatória do grupo.\n"
        "- `/instance` - Envia um GIF de bom dia abençoado.\n\n"
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

@app.on_message(filters.command("instance"))
async def cmd_instance(client, message):
    if not chat_autorizado(message.chat.id):
        return
    gifs_catolicos = [
        "https://media.tenor.com/tH2hPj0tK14AAAAC/bom-dia-deus.gif",
        "https://media.tenor.com/mO2X9g-T49QAAAAC/bom-dia.gif",
        "https://media.tenor.com/x4W9bNnsTlkAAAAC/bom-dia-catolico.gif",
        "https://media.tenor.com/1GvK_i8E0bIAAAAC/bom-dia-amigos.gif",
        "https://media.tenor.com/7s2NndU6_oYAAAAC/bom-dia-nossa-senhora.gif",
        "https://media.tenor.com/pZqM8lW_P7kAAAAC/bom-dia.gif",
        "https://media.tenor.com/r6_6gN9A0yQAAAAC/bom-dia.gif",
        "https://media.tenor.com/4h_R-A07_E4AAAAC/bom-dia.gif",
        "https://media.tenor.com/WvzldR40pgAAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/zhe-JWGvC3sAAAAM/jesus-god.gif",
        "https://media.tenor.com/TBeN43TlzMsAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/ESIuDKbQoLQAAAAM/bom-dia-bom-dia-retencao.gif",
        "https://media.tenor.com/t8U6B-f7AyUAAAAM/jesus-goodmorning.gif",
        "https://media.tenor.com/JdqOAJYXiRsAAAAM/bom-dia-que-o-nosso.gif",
        "https://media.tenor.com/N6XtQf397SwAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/2e614ZJXUq0AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/QJ55RdY_lnkAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/_yA0ZW7VjDYAAAAM/jesus-bible.gif",
        "https://media.tenor.com/kiJV0utmMoYAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/WbjAqTxVPloAAAAM/god-bless-you-jesus.gif",
        "https://media.tenor.com/VdoB274LopsAAAAM/bom-dia-valtatu%C3%AD-bom-dia.gif",
        "https://media.tenor.com/Swjz1L08ZR0AAAAM/bom-dia.gif",
        "https://media.tenor.com/EicZL9riOb0AAAAM/jesus-identidade.gif",
        "https://media.tenor.com/ozpe9Gew2zIAAAAM/good-morning.gif",
        "https://media.tenor.com/FRjJeOSeszkAAAAM/hand-jesus.gif",
        "https://media.tenor.com/MUNzKvQ6TI4AAAAM/good-morning-good-morning-jesus.gif",
        "https://media.tenor.com/meSKEFDMXl4AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/-et4j7grunEAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/EYMoO2zNXioAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/QqJt5KtuTAMAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/6E85v_z2UHcAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/uu_Aftj6DNQAAAAM/jesus-valtatui-hug.gif",
        "https://media.tenor.com/OQrAEaQ5f3YAAAAM/jesus-god.gif"
    ]
    gif_escolhido = random.choice(gifs_catolicos)
    try:
        await client.send_animation(message.chat.id, gif_escolhido, reply_to_message_id=message.id)
    except Exception as e:
        log.error(f"Erro ao enviar gif /instance: {e}")

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
COMANDOS = {"ranking", "bocadeleite", "anual", "stats", "help", "repetido", "id", "comi", "instance"}

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
        if user_id and not verificar_rate_limit(user_id):
            aviso = await message.reply_text("⏳ Rate limit! Máximo 10 links por minuto.")
            await asyncio.sleep(5)
            try:
                await aviso.delete()
            except Exception:
                pass
            return

        # DB registration for all platforms
        url_norm = urlunparse(urlparse(url_raw)._replace(query="")).lower().rstrip("/")
        repetido_db, info_db = db.registrar_link_e_checar(url_norm, message.from_user.first_name or "Membro", user_id)

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

                cap_limpa = limpar_texto(res.get('text', ''))
                legenda = f"📸 {cap_limpa}\n\nAutor: {res.get('user_name', 'Autor')}\n👤 Enviado por: {usuario}"

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
                                await msg_espera.edit_text(f"🚫 Vídeo muito longo! ({int(duracao_s // 60)}min). Limite: 5min.")
                                await asyncio.sleep(5)
                                await msg_espera.delete()
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
                                lista_telegram.append(InputMediaVideo(path, caption=legenda, supports_streaming=True))
                            except Exception as e:
                                log.error(f"X yt-dlp erro: {e}")
                                log.info(f"X: tentando URL direta: {video_url}")
                                try:
                                    lista_telegram.append(InputMediaVideo(video_url, caption=legenda))
                                except Exception:
                                    raise

                        if not lista_telegram:
                            raise Exception("Nenhuma midia encontrada.")

                        if len(lista_telegram) == 1:
                            midia = lista_telegram[0]
                            if isinstance(midia, InputMediaPhoto):
                                await client.send_photo(message.chat.id, midia.media, caption=midia.caption, reply_to_message_id=message.id)
                            else:
                                if isinstance(midia.media, str) and midia.media.startswith('http'):
                                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id)
                                else:
                                    await client.send_video(message.chat.id, midia.media, caption=midia.caption, supports_streaming=True, reply_to_message_id=message.id)
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
                    await msg_espera.delete()
                else:
                    log.info(f"X: tweet sem midia, enviando texto...")
                    cap_limpa = limpar_texto(res.get('text', ''))
                    msg = f"📝 {res.get('user_name', 'Autor')}:\n{cap_limpa}\n\n👤 Enviado por: {usuario}"
                    await message.reply_text(msg)
                    await msg_espera.delete()
                    log.info(f"Sucesso X (texto): {url_raw}")

                if repetido_db:
                    await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        except Exception as e:
            log.error(f"Erro X: {e}")
            await msg_espera.edit_text("❌ Falha ao processar post do X.")
            await asyncio.sleep(3)
        finally:
            for p in arquivos_x:
                if os.path.exists(p):
                    os.remove(p)
        return

    # 2. INSTAGRAM (handler dedicado)
    if url_raw and any(d in url_raw for d in ["instagram.com", "instagr.am"]):
        msg_espera = await message.reply_text("⏳ *Baixando do Instagram...*")
        await processar_instagram(client, message, url_raw, usuario, msg_espera)

        if repetido_db:
            await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        return

    # 3. YOUTUBE, TIKTOK, THREADS, PINTEREST (yt-dlp generico)
    if url_raw and any(d in url_raw for d in ["youtube.com", "youtu.be", "tiktok.com", "threads.net", "pinterest.com", "pin.it"]):
        url = url_raw
        yt_match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]+)', url)
        if yt_match:
            url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        elif not any(d in url for d in ["youtube.com", "youtu.be", "google.com"]):
            url = urlunparse(urlparse(url)._replace(query="")).rstrip("/")

        msg_espera = await message.reply_text("⏳ *Puxando mídia original...*")
        await extrair_e_enviar_midia(client, message, url, usuario, msg_espera)

        if repetido_db and not any(d in url_raw for d in ["youtube.com", "youtu.be"]):
            await enviar_aviso_duplicado(client, message, {}, info_db, usuario)
        return


    # 4. OUTROS LINKS (fallback)
    if url_raw and any(d in url_raw for d in DOMINIOS_PERMITIDOS):
        if not url_permitida(url_raw):
            return

        url = url_raw
        yt_match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]+)', url)
        if yt_match:
            url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        elif not any(d in url for d in ["youtube.com", "youtu.be", "google.com"]):
            url = urlunparse(urlparse(url)._replace(query="")).rstrip("/")

        url_curta = await encurtar_url(url) if len(url) > 60 else url

        msg_espera = await message.reply_text("⚙️ Processando...")
        await extrair_e_enviar_midia(client, message, url, usuario, msg_espera)

        if repetido_db and not any(d in url_raw for d in ["youtube.com", "youtu.be"]):
            await enviar_aviso_duplicado(client, message, {}, info_db, usuario)

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
    app.run()
