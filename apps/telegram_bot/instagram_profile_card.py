"""Gera um card de perfil do Instagram com altura ajustada ao conteúdo."""
import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps


BG_TOP = (28, 28, 40)
BG_BOTTOM = (10, 10, 18)
TEXTO_BRANCO = (245, 245, 245)
TEXTO_CINZA = (160, 160, 175)
TEXTO_MUTADO = (145, 145, 165)
AZUL_VERIFICADO = (0, 149, 246)
SEPARADOR = (50, 50, 70)
DESTAQUE = (255, 255, 255)

LARGURA_CARD = 720
MARGEM = 44
AVATAR_TAM = 176


def _caminho_fonte(nome: str) -> str | None:
    candidatos = [
        os.path.join(r"C:\Windows\Fonts", nome),
        os.path.join("/usr/share/fonts/truetype/dejavu", nome),
        os.path.join("/usr/share/fonts/truetype/liberation", nome),
        os.path.join("/usr/share/fonts/truetype/liberation2", nome),
        os.path.join("/System/Library/Fonts", nome),
        os.path.join("/System/Library/Fonts/Supplemental", nome),
    ]
    return next((c for c in candidatos if os.path.exists(c)), None)


_FONTE_CACHE: dict = {}
_DEF_FONTES = {
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
}


def _get_font(tipo: str, tamanho: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    chave = (tipo, tamanho)
    if chave not in _FONTE_CACHE:
        # O fallback só acontece depois de tentar todas as fontes. Antes, a
        # ausência da primeira fonte Windows encerrava a busca com bitmap 10px.
        for nome in _DEF_FONTES[tipo]:
            try:
                fonte = ImageFont.truetype(_caminho_fonte(nome) or nome, tamanho)
            except (OSError, ValueError):
                continue
            _FONTE_CACHE[chave] = fonte
            break
        else:
            try:
                _FONTE_CACHE[chave] = ImageFont.load_default(size=tamanho)
            except TypeError:  # Compatibilidade com Pillow anterior a 10.1.
                _FONTE_CACHE[chave] = ImageFont.load_default()
    return _FONTE_CACHE[chave]


def _limitar_texto(draw, texto: str, fonte, max_largura: int, forcar: bool = False) -> str:
    if not forcar and draw.textlength(texto, font=fonte) <= max_largura:
        return texto
    texto = texto.rstrip()
    while texto and draw.textlength(texto + "…", font=fonte) > max_largura:
        texto = texto[:-1].rstrip()
    return texto + "…"


def _quebrar_texto(draw, texto: str, fonte, max_largura: int, max_linhas: int | None = None) -> list[str]:
    """Respeita parágrafos, quebra URLs e limita as linhas com reticências."""
    linhas: list[str] = []
    for paragrafo in texto.splitlines() or [""]:
        atual = ""
        for palavra in paragrafo.split():
            teste = f"{atual} {palavra}".strip()
            if draw.textlength(teste, font=fonte) <= max_largura:
                atual = teste
                continue
            if atual:
                linhas.append(atual)
                atual = ""
            while draw.textlength(palavra, font=fonte) > max_largura and len(palavra) > 1:
                fim = 1
                while fim < len(palavra) and draw.textlength(palavra[:fim + 1], font=fonte) <= max_largura:
                    fim += 1
                linhas.append(palavra[:fim])
                palavra = palavra[fim:]
            atual = palavra
        linhas.append(atual)
    if max_linhas and len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        linhas[-1] = _limitar_texto(draw, linhas[-1], fonte, max_largura, forcar=True)
    return linhas or [""]


def _avatar_circular(imagem: Image.Image, tamanho: int) -> Image.Image:
    """Recorta pelo centro sem distorcer e mantém a máscara no canal alfa."""
    foto = ImageOps.fit(ImageOps.exif_transpose(imagem).convert("RGBA"), (tamanho, tamanho), method=Image.Resampling.LANCZOS)
    mascara = Image.new("L", (tamanho, tamanho), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, tamanho - 1, tamanho - 1), fill=255)
    foto.putalpha(mascara)
    return foto


def _badge_verificado(draw, x: int, cy: int, r: int) -> int:
    draw.ellipse((x, cy - r, x + 2 * r, cy + r), fill=AZUL_VERIFICADO)
    draw.line(
        [(x + r * 0.48, cy), (x + r * 0.88, cy + r * 0.35), (x + r * 1.52, cy - r * 0.40)],
        fill=DESTAQUE, width=3,
    )
    return 2 * r


def _formatar(valor) -> str:
    if valor is None:
        return "—"
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return str(valor)[:40]
    for divisor, unidade in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, " mil")):
        if numero >= divisor:
            return f"{numero / divisor:.1f}".removesuffix(".0").replace(".", ",") + unidade
    return str(numero)


def montar_card(profile: dict, foto_bytes: bytes | None) -> bytes:
    """Retorna PNG; dados não obtidos são distintos de campos vazios/zero."""
    largura = LARGURA_CARD
    util = largura - 2 * MARGEM
    medidor = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    nome_fonte = _get_font("bold", 38)
    user_fonte = _get_font("regular", 27)
    bio_fonte = _get_font("regular", 26)
    stat_num = _get_font("bold", 36)
    stat_label = _get_font("regular", 20)
    tag_fonte = _get_font("regular", 22)

    nome = str(profile.get("full_name") or profile.get("username") or "Perfil")[:256]
    x_texto = MARGEM + AVATAR_TAM + 28
    largura_texto = largura - MARGEM - x_texto
    verificado = bool(profile.get("is_verified"))
    nomes = _quebrar_texto(medidor, nome, nome_fonte, largura_texto - (40 if verificado else 0), 2)
    usuario = _limitar_texto(medidor, "@" + str(profile.get("username") or "")[:128], user_fonte, largura_texto)
    tags = []
    if profile.get("is_private"):
        tags.append("Privado")
    if profile.get("is_business"):
        tags.append("Conta comercial")
    if profile.get("category"):
        tags.append(str(profile["category"])[:256])
    linhas_tags = _quebrar_texto(medidor, " • ".join(tags), tag_fonte, largura_texto, 2) if tags else []
    altura_texto = len(nomes) * 46 + 10 + 34 + (12 + len(linhas_tags) * 29 if linhas_tags else 0)
    y_bio = MARGEM + max(AVATAR_TAM, altura_texto) + 32

    bio = profile.get("biography")
    if bio is None or profile.get("biography_available") is False:
        bio = "Bio indisponível."
    else:
        bio = str(bio).strip() or "Sem bio."
    linhas_bio = _quebrar_texto(medidor, bio[:2000], bio_fonte, util, 6)
    y_sep = y_bio + len(linhas_bio) * 36 + 18
    y_stat = y_sep + 28
    fim_stats = y_stat + 78

    rodapes = []
    if profile.get("reels") is not None:
        rodapes.append("Reels: " + _formatar(profile["reels"]))
    if profile.get("external_url"):
        rodapes.extend(_quebrar_texto(medidor, str(profile["external_url"])[:1000], tag_fonte, util, 2))
    if profile.get("partial"):
        rodapes.append("Dados parciais do Instagram")
    y_rodape = fim_stats + 32
    altura = (y_rodape + len(rodapes) * 30 if rodapes else fim_stats) + MARGEM

    img = Image.new("RGB", (largura, altura), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(altura):
        t = y / max(altura - 1, 1)
        cor = tuple(int(BG_TOP[c] + (BG_BOTTOM[c] - BG_TOP[c]) * t) for c in range(3))
        draw.line([(0, y), (largura, y)], fill=cor)

    foto_ok = False
    if foto_bytes:
        try:
            with Image.open(io.BytesIO(foto_bytes)) as foto:
                circulo = _avatar_circular(foto, AVATAR_TAM)
            img.paste(circulo, (MARGEM, MARGEM), circulo)
            draw.ellipse(
                (MARGEM - 2, MARGEM - 2, MARGEM + AVATAR_TAM + 1, MARGEM + AVATAR_TAM + 1),
                outline=(70, 70, 90), width=3,
            )
            foto_ok = True
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if not foto_ok:
        cx = cy = MARGEM + AVATAR_TAM / 2
        draw.ellipse((MARGEM, MARGEM, MARGEM + AVATAR_TAM, MARGEM + AVATAR_TAM), fill=(40, 40, 60))
        inicial = nome[:1].upper()
        letra_fonte = _get_font("bold", 84)
        bbox = letra_fonte.getbbox(inicial)
        draw.text((cx - (bbox[2] - bbox[0]) / 2 - bbox[0], cy - (bbox[3] - bbox[1]) / 2 - bbox[1]), inicial, fill=TEXTO_BRANCO, font=letra_fonte)

    y_texto = MARGEM + 4
    for linha in nomes:
        draw.text((x_texto, y_texto), linha, fill=DESTAQUE, font=nome_fonte, anchor="lt")
        y_texto += 46
    if verificado:
        nome_w = draw.textlength(nomes[0], font=nome_fonte)
        _badge_verificado(draw, x_texto + nome_w + 10, MARGEM + 22, 14)
    draw.text((x_texto, y_texto + 10), usuario, fill=TEXTO_CINZA, font=user_fonte, anchor="lt")
    y_texto += 56
    for linha in linhas_tags:
        draw.text((x_texto, y_texto), linha, fill=TEXTO_MUTADO, font=tag_fonte, anchor="lt")
        y_texto += 29

    for i, linha in enumerate(linhas_bio):
        draw.text((MARGEM, y_bio + i * 36), linha, fill=TEXTO_BRANCO, font=bio_fonte, anchor="lt")
    draw.line([(MARGEM, y_sep), (largura - MARGEM, y_sep)], fill=SEPARADOR, width=2)

    for i, (label, campo) in enumerate((("POSTS", "posts"), ("SEGUIDORES", "followers"), ("SEGUINDO", "following"))):
        cx = MARGEM + util * (i + 0.5) / 3
        numero = _limitar_texto(draw, _formatar(profile.get(campo)), stat_num, util / 3 - 16)
        cor = AZUL_VERIFICADO if i == 0 else DESTAQUE
        draw.text((cx - draw.textlength(numero, font=stat_num) / 2, y_stat), numero, fill=cor, font=stat_num, anchor="lt")
        draw.text((cx - draw.textlength(label, font=stat_label) / 2, y_stat + 54), label, fill=TEXTO_MUTADO, font=stat_label, anchor="lt")

    if rodapes:
        draw.line([(MARGEM, fim_stats + 16), (largura - MARGEM, fim_stats + 16)], fill=SEPARADOR, width=2)
        for i, linha in enumerate(rodapes):
            draw.text((MARGEM, y_rodape + i * 30), linha, fill=TEXTO_CINZA, font=tag_fonte, anchor="lt")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def gerar_card(profile: dict, foto_bytes: bytes | None) -> bytes:
    """Ponto de entrada compatível com a execução em thread do bot."""
    return montar_card(profile, foto_bytes)
