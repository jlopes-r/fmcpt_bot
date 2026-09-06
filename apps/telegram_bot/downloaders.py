import asyncio
import logging
import os
import time
import json
import sys
import signal
import shutil
import tempfile
from pathlib import Path
from contextvars import ContextVar
from functools import wraps
from collections.abc import Callable
from functools import partial

import aiohttp
import yt_dlp

log = logging.getLogger(__name__)

# Player clients mais robustos para contornar o bloqueio "confirm you're not a
# bot" / "please sign in" do YouTube em vídeos específicos.
YOUTUBE_CLIENTS_FALLBACK = ("tv", "ios", "mweb", "android")

# Timeout máximo da chamada ao yt-dlp (extração + download). Sem timeout, um
# vídeo bloqueado podia prender a thread e "enrolar" os downloads seguintes.
YDLP_TIMEOUT = 7200  # segundos (2 horas); downloads longos podem ser cancelados
DISK_RESERVE = 768 * 1024 * 1024
_download_lock = asyncio.Lock()
ACTIVE_DIRECTORIES = set()
_jobs = ContextVar('download_jobs', default=None)


def managed_downloads(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        jobs = []
        token = _jobs.set(jobs)
        try:
            return await function(*args, **kwargs)
        finally:
            for info in jobs:
                release_job(info)
            _jobs.reset(token)
    return wrapped


class DownloadCancelled(Exception):
    """Download interrompido por solicitação do usuário."""

# Formato preferido: mp4 com vídeo h264 (avc1) + áudio m4a, para garantir que o
# Telegram consiga reproduzir sem reprocessar. Cai para mp4 genérico, depois
# para qualquer formato como último recurso.
FORMATO_MP4_H264 = (
    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
    "/best[ext=mp4]/best"
)

# Opções seguras aplicadas a TODA chamada (o chamador pode sobrescrever).
_OPCOES_SEGURAS = {
    "noplaylist": True,
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 30,
    "nocheckcertificate": True,
    "no_color": True,
    "quiet": True,
    "no_warnings": True,
    "merge_output_format": "mp4",
}


def limite_duracao_filter(limite_segundos: int):
    def _filter(info_dict, *, incomplete):
        duracao = info_dict.get("duration")
        if duracao and duracao > limite_segundos:
            return f"Video tem {duracao}s, acima do limite de {limite_segundos}s"
        return None

    _filter.duration_limit = limite_segundos
    return _filter


def video_exige_confirmacao(duracao, limite_segundos: int) -> bool:
    """Informa se um vídeo deve aguardar confirmação antes do download."""
    try:
        return float(duracao or 0) > limite_segundos
    except (TypeError, ValueError):
        return False


def _aplicar_opcoes_seguras(ydl_opts: dict) -> dict:
    """Mescla as opções do chamador com as defaults de segurança.

    O chamador tem prioridade em qualquer chave que definir; as defaults só
    preenchem o que não foi especificado.
    """
    merged = dict(_OPCOES_SEGURAS)
    merged.update(ydl_opts)
    return merged


def processar_com_ytdlp(url, ydl_opts):
    """Executa o yt-dlp com opções de segurança mescladas."""
    with yt_dlp.YoutubeDL(_aplicar_opcoes_seguras(ydl_opts)) as ydl:
        return ydl.extract_info(url, download=True)


def _progresso_ytdlp_sync(msg_espera, loop, cancel_event=None, reply_markup=None):
    """Cria um progress_hooks do yt-dlp que agrega o progresso à mensagem.

    O hook roda numa thread do executor (fora do event loop). Por isso usamos
    run_coroutine_threadsafe para agendar a edição da mensagem no loop correto.
    Retorna um callable sync aceito pelo yt-dlp.
    """
    estado = {"ultimo_pct": 0, "ultimo_tempo": 0}

    def _hook(d):
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Download cancelado pelo usuário")
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if not total:
            return
        pct = int(downloaded * 100 / total)
        agora = time.time()
        if (
            pct - estado["ultimo_pct"] >= 10 and agora - estado["ultimo_tempo"] > 1.5
        ) or pct == 100:
            if pct == 100 and estado["ultimo_pct"] == 100:
                return
            estado["ultimo_pct"] = pct
            estado["ultimo_tempo"] = agora
            if msg_espera is not None:
                asyncio.run_coroutine_threadsafe(
                    _atualizar_barra_download(msg_espera, pct, reply_markup),
                    loop,
                )

    return _hook


async def _atualizar_barra_download(msg_espera, pct: int, reply_markup=None) -> None:
    try:
        barra = "█" * (pct // 10) + "░" * (10 - pct // 10)
        await msg_espera.edit_text(
            f"⬇️ Baixando... {barra} {pct}%",
            reply_markup=reply_markup,
        )
    except Exception:
        pass


def processar_com_fallback(url, ydl_opts, msg_espera=None, loop=None, cancel_event=None, reply_markup=None):
    """Roda o yt-dlp; se o YouTube bloquear o vídeo, tenta de novo com outros
    player clients mais robustos (contorna "sign in"/verificação)."""
    opts = _aplicar_opcoes_seguras(ydl_opts or {})
    if msg_espera is not None and loop is not None:
        opts["progress_hooks"] = [
            *(opts.get("progress_hooks") or []),
            _progresso_ytdlp_sync(msg_espera, loop, cancel_event, reply_markup),
        ]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Exception as e:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Download cancelado pelo usuário") from e
        if "youtube.com" not in url:
            raise
        log.warning(
            "yt-dlp falhou com os clients atuais (%s); tentando fallback %s",
            e, YOUTUBE_CLIENTS_FALLBACK,
        )
        fallback_opts = dict(opts)
        fallback_opts["extractor_args"] = {
            "youtube": {"player_client": list(YOUTUBE_CLIENTS_FALLBACK)}
        }
        with yt_dlp.YoutubeDL(_aplicar_opcoes_seguras(fallback_opts)) as ydl2:
            return ydl2.extract_info(url, download=True)


async def baixar_com_ytdlp(
    url, ydl_opts, timeout: float | None = None, msg_espera=None,
    cancel_event=None, reply_markup=None,
):
    """Run an isolated worker with cancellation, deadline and disk budget."""
    # Serialize heavy transfers on the small VM. Waiting is cancellable too.
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        try:
            await asyncio.wait_for(_download_lock.acquire(), .25)
            break
        except asyncio.TimeoutError:
            continue
    directory = None
    proc = None
    success = False
    reader = None
    try:
        root = Path(ydl_opts.get('paths', {}).get('home', '.')).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < DISK_RESERVE * 2:
            raise RuntimeError('Espaço livre insuficiente para baixar e montar a mídia.')
        directory = Path(tempfile.mkdtemp(prefix='job_', dir=root))
        ACTIVE_DIRECTORIES.add(str(directory))
        opts = dict(ydl_opts)
        duration_filter = opts.pop('match_filter', None)
        if duration_filter:
            opts['_duration_limit'] = getattr(duration_filter, 'duration_limit', 600)
        opts['paths'] = {'home': str(directory), 'temp': str(directory)}
        opts['outtmpl'] = str(directory / '%(id)s_%(autonumber)s.%(ext)s')
        opts['noplaylist'] = True
        opts.pop('progress_hooks', None)
        kwargs = {'start_new_session': True} if os.name != 'nt' else {}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'apps.telegram_bot.download_worker',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
            cwd=str(Path(__file__).resolve().parents[2]), **kwargs,
        )
        proc.stdin.write((json.dumps({'url': url, 'options': opts}) + '\n').encode())
        await proc.stdin.drain()
        proc.stdin.close()
        result = None
        async def consume():
            nonlocal result
            last_update = 0.0
            async for line in proc.stdout:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if 'result' in event:
                    result = event['result']
                if 'error' in event:
                    raise yt_dlp.utils.DownloadError(event['error'])
                if 'progress' in event and msg_espera is not None and time.monotonic() - last_update > 3:
                    last_update = time.monotonic()
                    if cancel_event is None or not cancel_event.is_set():
                        try:
                            await msg_espera.edit_text(f"⬇️ Baixando: {event['progress'] / 1048576:.1f} MiB", reply_markup=reply_markup)
                        except Exception:
                            pass
        reader = asyncio.create_task(consume())
        deadline = time.monotonic() + (timeout or YDLP_TIMEOUT)
        while proc.returncode is None:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            if time.monotonic() > deadline:
                raise asyncio.TimeoutError('Tempo limite do download excedido')
            if shutil.disk_usage(directory).free < DISK_RESERVE:
                raise RuntimeError('Download interrompido para evitar disco cheio.')
            size = 0
            for p in directory.rglob('*'):
                try:
                    if p.is_file():
                        size += p.stat().st_size
                except FileNotFoundError:
                    pass  # ffmpeg/yt-dlp can rename files while being measured.
            if size > int(opts.get('max_filesize') or 5_000_000_000) * 2:
                raise RuntimeError('Arquivos temporários excederam o orçamento de disco.')
            await asyncio.sleep(.2)
        await reader
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        if proc.returncode or not result:
            raise RuntimeError('O extrator não retornou mídia válida.')
        limit = int(opts.get('max_filesize') or 5_000_000_000)
        if any(p.stat().st_size > limit for p in directory.rglob('*') if p.is_file()):
            raise RuntimeError('Arquivo final excede o limite configurado.')
        result['_job_directory'] = str(directory)
        if _jobs.get() is not None:
            _jobs.get().append(result)
        success = True
        return result
    finally:
        if proc is not None:
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.returncode is None:
                killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(proc.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            await proc.wait()
        if reader is not None:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if directory is not None:
            if not success:
                shutil.rmtree(directory)
            if not success:
                ACTIVE_DIRECTORIES.discard(str(directory))
        _download_lock.release()


def release_job(info):
    directory = (info or {}).get('_job_directory')
    if directory and directory in ACTIVE_DIRECTORIES:
        shutil.rmtree(directory, ignore_errors=True)
        ACTIVE_DIRECTORIES.discard(directory)


async def baixar_url_limitado(
    session: aiohttp.ClientSession,
    url: str,
    destino: str,
    limite_bytes: int,
    timeout: float = 120,
    headers: dict | None = None,
    on_response: Callable[[aiohttp.ClientResponse], None] | None = None,
) -> str:
    """Baixa uma URL para um arquivo em streaming, abortando se exceder o limite.

    Diferente de `await resp.read()` (que lê tudo de uma vez para a RAM e ignora
    o tamanho), aqui processamos em pedaços de 1MB e paramos assim que o limite
    for ultrapassado. Se o servidor já anunciar o tamanho no header, aborta antes
    mesmo de começar a gravar.

    Levanta Exception se o arquivo for grande demais ou o download falhar.
    """
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            resp.raise_for_status()
            if on_response:
                on_response(resp)
            content_length = resp.content_length
            if content_length and content_length > limite_bytes:
                raise Exception(
                    f"Arquivo muito grande ({content_length / 1024 / 1024:.0f}MB > limite)"
                )
            total = 0
            with open(destino, "wb") as arquivo:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if shutil.disk_usage(Path(destino).parent).free < DISK_RESERVE:
                        raise RuntimeError('Espaço em disco insuficiente.')
                    total += len(chunk)
                    if total > limite_bytes:
                        raise Exception(
                            f"Arquivo muito grande (> {limite_bytes / 1024 / 1024:.0f}MB)"
                        )
                    arquivo.write(chunk)
        return destino
    except Exception:
        try:
            os.remove(destino)
        except OSError:
            pass
        raise


def caminho_baixado(item: dict) -> str | None:
    if "requested_downloads" in item:
        for download in item["requested_downloads"]:
            path = download.get("filepath")
            if path and os.path.exists(path):
                return path
    path = item.get("filepath")
    if path and os.path.exists(path):
        return path
    return None
