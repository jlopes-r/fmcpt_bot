import re


def limpar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r"#\w+", "", texto)
    texto = re.sub(r"\n\s*\n", "\n\n", texto)
    return texto.strip()


def montar_legenda(texto_base: str, autor: str, usuario: str, emoji: str = "✨", limite: int = 1024) -> str:
    """Monta legenda respeitando o limite de caracteres do Telegram."""
    sufixo = f"\n\nAutor: {autor}\n👤 Enviado por: {usuario}"
    espaco_disponivel = limite - len(sufixo) - len(emoji) - 5
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
        corte = texto.rfind("\n", 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = texto.rfind(" ", 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = limite
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip()
    return partes
