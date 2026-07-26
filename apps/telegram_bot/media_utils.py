import time


def detectar_extensao(url: str, content_type: str = "") -> str:
    """Detecta extensão de arquivo a partir do Content-Type e/ou URL."""
    if content_type:
        ct = content_type.lower().split(";")[0].strip()
        ct_map = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            "image/heic": "heic",
            "image/heif": "heif",
            "video/mp4": "mp4",
            "video/quicktime": "mov",
            "video/webm": "webm",
        }
        if ct in ct_map:
            return ct_map[ct]

    extensoes_validas = {"jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "m4v", "webm", "heic"}
    try:
        path_sem_query = url.split("?")[0]
        ultimo_segmento = path_sem_query.rsplit("/", 1)[-1]
        if "." in ultimo_segmento:
            ext = ultimo_segmento.rsplit(".", 1)[-1].lower()
            if ext in extensoes_validas:
                return ext
    except Exception:
        pass

    return "jpg"


def progresso_upload(msg_espera):
    """Cria um callback de progresso para upload de vídeo."""
    estado = {"ultimo_pct": 0, "ultimo_tempo": 0}

    async def _callback(current, total):
        if total == 0:
            return
        pct = int(current * 100 / total)
        agora = time.time()

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
