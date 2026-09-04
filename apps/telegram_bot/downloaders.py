import asyncio
import logging
import os
import time
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
YDLP_TIMEOUT = 300  # segundos (5 minutos)

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

    return _filter


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


def _progresso_ytdlp_sync(msg_espera, loop):
    """Cria um progress_hooks do yt-dlp que agrega o progresso à mensagem.

    O hook roda numa thread do executor (fora do event loop). Por isso usamos
    run_coroutine_threadsafe para agendar a edição da mensagem no loop correto.
    Retorna um callable sync aceito pelo yt-dlp.
    """
    estado = {"ultimo_pct": 0, "ultimo_tempo": 0}

    def _hook(d):
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
                    _atualizar_barra_download(msg_espera, pct),
                    loop,
                )

    return _hook


async def _atualizar_barra_download(msg_espera, pct: int) -> None:
    try:
        barra = "█" * (pct // 10) + "░" * (10 - pct // 10)
        await msg_espera.edit_text(
            f"⬇️ Baixando... {barra} {pct}%"
        )
    except Exception:
        pass


def processar_com_fallback(url, ydl_opts, msg_espera=None, loop=None):
    """Roda o yt-dlp; se o YouTube bloquear o vídeo, tenta de novo com outros
    player clients mais robustos (contorna "sign in"/verificação)."""
    opts = _aplicar_opcoes_seguras(ydl_opts or {})
    if msg_espera is not None and loop is not None:
        opts["progress_hooks"] = [*(opts.get("progress_hooks") or []), _progresso_ytdlp_sync(msg_espera, loop)]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Exception as e:
        if "youtube.com" not in url:
            raise
        log.warning(
            "yt-dlp falhou com os clients atuais (%s); tentando fallback %s",
            e, YOUTUBE_CLIENTS_FALLBACK,
        )
        fallback_opts = dict(ydl_opts)
        fallback_opts["extractor_args"] = {
            "youtube": {"player_client": list(YOUTUBE_CLIENTS_FALLBACK)}
        }
        with yt_dlp.YoutubeDL(_aplicar_opcoes_seguras(fallback_opts)) as ydl2:
            return ydl2.extract_info(url, download=True)


async def baixar_com_ytdlp(url, ydl_opts, timeout: float | None = None, msg_espera=None):
    """Executa o yt-dlp em thread com timeout garantido e progresso opcional.

    Se a chamada estourar o tempo (vídeo bloqueado/enrolado), levanta
    asyncio.TimeoutError e libera o fluxo, evitando que o download prenda o bot.
    """
    timeout = timeout or YDLP_TIMEOUT
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(
        None, partial(processar_com_fallback, url, ydl_opts, msg_espera, loop)
    )
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        log.error("yt-dlp excedeu o timeout de %.0fs para %s", timeout, url)
        raise


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
