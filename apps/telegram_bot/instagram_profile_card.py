"""
Gerador de card estilizado para o perfil do Instagram.

Dado o dict de perfil (+ bytes da foto), monta uma imagem PNG com fundo
degradê, avatar circular, nome, selo de verificação, bio quebrada em linhas
e as métricas (posts/followers/following). O card é enviado pelo Telegram em
vez de um print de texto sem graça.
"""
import io
import os
import logging

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("SuperBot")

# ─── Cores (paleta escura estilo IG) ─────────────────────────────────────────
BG_TOP = (28, 28, 40)
BG_BOTTOM = (10, 10, 18)
TEXTO_BRANCO = (245, 245, 245)
TEXTO_CINZA = (160, 160, 175)
TEXTO_MUTADO = (120, 120, 140)
AZUL_VERIFICADO = (0, 149, 246)
SEPARADOR = (50, 50, 70)
DESTAQUE = (255, 255, 255)

LARGURA_CARD = 720
MARGEM = 54
AVATAR_TAM = 216


def _caminho_fonte(nome: str) -> str | None:
    # Procura uma font TrueType confiável em várias plataformas.
    candidatos = [
        os.path.join(r"C:\Windows\Fonts", nome),
        os.path.join("/usr/share/fonts/truetype/dejavu", nome),
        os.path.join("/usr/share/fonts/truetype/liberation", nome),
        os.path.join("/System/Library/Fonts", nome),
    ]
    for c in candidatos:
        if os.path.exists(c):
            return c
    return None


def _fonte(nome, tamanho: int, negrito: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Carrega uma fonte TTF, com fallback para a bitmel+default do PIL."""
    if negrito and "-Bold." not in nome and not nome.upper().startswith("ARIALBD"):
        pass
    caminho = _caminho_fonte(nome)
    if caminho:
        try:
            return ImageFont.truetype(caminho, tamanho)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# Fontes selecionadas uma vez (evita re-leitura a cada chamada)
_FONTE_CACHE: dict = {}
_DEF_FONTES = {
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
}


def _get_font(tipo: str, tamanho: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    chave = (tipo, tamanho)
    if chave in _FONTE_CACHE:
        return _FONTE_CACHE[chave]
    for nome in _DEF_FONTES[tipo]:
        f = _fonte(nome, tamanho)
        if len(f.getbbox("Ag")) > 0:  # garante que há glifos
            _FONTE_CACHE[chave] = f
            return f
    f = _fonte("arial.ttf", tamanho)
    _FONTE_CACHE[chave] = f
    return f


def _quebrar_texto(draw, texto: str, fonte, max_largura: int) -> list[str]:
    """Quebra o texto em linhas que cabem em max_largura (pixels)."""
    palavras = texto.split()
    linhas: list[str] = []
    atual = ""
    for palavra in palavras:
        teste = f"{atual} {palavra}".strip()
        if draw.textlength(teste, font=fonte) <= max_largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


def _avatar_circular(imagem: Image.Image, tamanho: int) -> Image.Image:
    """Recorta a foto como avatar circular com borda leve."""
    foto = imagem.convert("RGB").resize(
        (tamanho, tamanho), Image.LANCZOS
    )
    mascara = Image.new("L", (tamanho, tamanho), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, tamanho - 1, tamanho - 1), fill=255)
    return foto


def _badge_verificado(draw, x: int, cy: int, r: int) -> int:
    """Desenha o selo azul de verificação e retorna a largura usada."""
    x0, y0, x1, y1 = x, cy - r, x + 2 * r, cy + r
    draw.ellipse((x0, y0, x1, y1), fill=AZUL_VERIFICADO)
    draw.line(
        [(x + r * 0.30, cy), (x + r * 0.45, cy + r * 0.16), (x + r * 0.72, cy - r * 0.14)],
        fill=(255, 255, 255), width=3,
    )
    return 2 * r


def montar_card(profile: dict, foto_bytes: bytes | None) -> bytes:
    """Gera o card e retorna os bytes da imagem PNG.

    Se foto_bytes for None, usa um círculo com a inicial do nome como avatar.
    """
    largura = LARGURA_CARD
    altura = 780

    img = Image.new("RGB", (largura, altura), BG_TOP)
    draw = ImageDraw.Draw(img)

    # ── Fundo degradê vertical ──
    for y in range(altura):
        t = y / max(altura - 1, 1)
        cor = tuple(
            int(BG_TOP[c] + (BG_BOTTOM[c] - BG_TOP[c]) * t)
            for c in range(3)
        )
        draw.line([(0, y), (largura, y)], fill=cor)

    nome_fonte = _get_font("bold", 46)
    user_fonte = _get_font("regular", 30)
    bio_fonte = _get_font("regular", 26)
    stat_num = _get_font("bold", 38)
    stat_label = _get_font("regular", 22)
    tag_fonte = _get_font("regular", 22)

    # ── Avatar ──
    avatar_x = MARGEM
    avatar_y = 70
    if foto_bytes:
        try:
            foto = Image.open(io.BytesIO(foto_bytes)).convert("RGB")
            circulo = _avatar_circular(foto, AVATAR_TAM)
            img.paste(circulo, (avatar_x, avatar_y), circulo)
            # Borda sutil em volta do círculo
            draw.ellipse(
                (avatar_x - 3, avatar_y - 3,
                 avatar_x + AVATAR_TAM + 3, avatar_y + AVATAR_TAM + 3),
                outline=(70, 70, 90), width=4,
            )
        except Exception:
            foto_bytes = None
    if not foto_bytes:
        # Avatar fallback: círculo com a inicial
        cy = avatar_y + AVATAR_TAM // 2
        cx = avatar_x + AVATAR_TAM // 2
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + AVATAR_TAM, avatar_y + AVATAR_TAM),
            fill=(40, 40, 60),
        )
        inicial = (profile.get("full_name") or profile.get("username") or "?")[:1].upper()
        letra_fonte = _get_font("bold", 110)
        bbox = letra_fonte.getbbox(inicial)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw / 2, cy - 40), inicial, fill=TEXTO_BRANCO, font=letra_fonte)

    # ── Nome + selo ──
    nome = profile.get("full_name") or profile.get("username") or "Perfil"
    # Corta nome se muito grande
    nome_visivel = nome
    while draw.textlength(nome_visivel, font=nome_fonte) > largura - 2 * MARGEM and len(nome_visivel) > 1:
        nome_visivel = nome_visivel[:-1]
    x_texto = avatar_x + AVATAR_TAM + 44
    y_texto = avatar_y + 18
    nome_w = draw.textlength(nome_visivel, font=nome_fonte)
    draw.text((x_texto, y_texto), nome_visivel, fill=DESTAQUE, font=nome_fonte)

    if profile.get("is_verified"):
        badge_x = x_texto + nome_w + 16
        r = 24
        cx_b = badge_x + r
        cy_b = y_texto + 46 // 2
        _badge_verificado(draw, x_texto + nome_w + 16, cy_b, r)

    # ── @username ──
    usr = f"@{profile.get('username') or ''}"
    draw.text((x_texto, y_texto + 60), usr, fill=TEXTO_CINZA, font=user_fonte)

    # ── Privado/business/categoria ──
    tags = []
    if profile.get("is_private"):
        tags.append("Privado")
    if profile.get("is_business"):
        tags.append("Conta comercial")
    if profile.get("category"):
        tags.append(str(profile["category"]))
    if tags:
        jon = "  •  ".join(tags)
        draw.text((x_texto, y_texto + 104), jon, fill=TEXTO_MUTADO, font=tag_fonte)

    # ── Bio ──
    bio = profile.get("biography") or "Sem bio."
    y_bio = avatar_y + AVATAR_TAM + 40
    for linha in _quebrar_texto(draw, bio, bio_fonte, largura - 2 * MARGEM):
        draw.text((avatar_x, y_bio), linha, fill=TEXTO_BRANCO, font=bio_fonte)
        y_bio += 36

    # ── Separador ──
    y_sep = y_bio + 18
    draw.line([(avatar_x, y_sep), (largura - MARGEM, y_sep)], fill=SEPARADOR, width=2)

    # ── Estatísticas ──
    def _formatar(valor):
        if valor is None:
            return "N/A"
        try:
            valor = int(valor)
        except (TypeError, ValueError):
            return str(valor)
        if valor >= 1_000_000:
            return f"{valor / 1_000_000:.1f}M"
        if valor >= 1_000:
            return f"{valor / 1_000:.1f}k"
        return str(valor)

    stats = [
        ("Posts", profile.get("posts")),
        ("Seguidores", profile.get("followers")),
        ("Seguindo", profile.get("following")),
    ]
    n = len(stats)
    espaco = (largura - 2 * MARGEM) / n
    y_stat = y_sep + 34
    for i, (label, valor) in enumerate(stats):
        cx = MARGEM + espaco * i + espaco / 2
        txt_num = _formatar(valor)
        num_w = draw.textlength(txt_num, font=stat_num)
        if i == 0:
            draw.text((cx - num_w / 2, y_stat), txt_num, fill=AZUL_VERIFICADO, font=stat_num)
        else:
            draw.text((cx - num_w / 2, y_stat), txt_num, fill=DESTAQUE, font=stat_num)
        label_w = draw.textlength(label, font=stat_label)
        draw.text((cx - label_w / 2, y_stat + 52), label.upper(), fill=TEXTO_MUTADO, font=stat_label)

    # ── Rodapé: link externo / reels ──
    rodapes = []
    if profile.get("reels") is not None:
        rodapes.append(f"Reels: {_formatar(profile['reels'])}")
    if profile.get("external_url"):
        rodapes.append(str(profile["external_url"]))
    if rodapes:
        draw.line([(avatar_x, y_stat + 108), (largura - MARGEM, y_stat + 108)], fill=SEPARADOR, width=2)
        y_rod = y_stat + 122
        for rod in rodapes:
            draw.text((avatar_x, y_rod), rod, fill=TEXTO_CINZA, font=tag_fonte)
            y_rod += 32

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def gerar_card(profile: dict, foto_bytes: bytes | None) -> bytes:
    """Envolve montar_card em thread segura (CPU-bound)."""
    return montar_card(profile, foto_bytes)