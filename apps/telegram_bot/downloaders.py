import os

import yt_dlp


def limite_duracao_filter(limite_segundos: int):
    def _filter(info_dict, *, incomplete):
        duracao = info_dict.get("duration")
        if duracao and duracao > limite_segundos:
            return f"Video tem {duracao}s, acima do limite de {limite_segundos}s"
        return None

    return _filter


def processar_com_ytdlp(url, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


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
