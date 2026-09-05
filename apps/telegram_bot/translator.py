import re
import time
import logging
from collections import Counter

from langdetect import detect_langs, DetectorFactory

try:
    from deep_translator import GoogleTranslator
except Exception:  # pragma: no cover
    GoogleTranslator = None

log = logging.getLogger(__name__)

# Detecção determinística entre processos
DetectorFactory.seed = 0

# Idiomas que NÃO devem ser traduzidos:
#   - "pt"  : já é o idioma alvo (mantém como está)
#   - "en"  : o usuário quer manter o texto original em inglês
IDIOMAS_SEM_TRADUCAO = ("pt", "en")

# Padrões que indicam que o site de tradução retornou uma página de erro
# em vez de uma tradução válida (ex: "Error 500 (Server Error)!!...").
_PADRAO_ERRO_TRADUCAO = re.compile(
    r"(server error|that's an error|<html|<head|<!doctype|error\s*5\d\d|http\s+5\d\d)",
    re.IGNORECASE,
)

# Tentativas de tradução antes de desistir (retry com backoff).
MAX_TENTATIVAS_TRADUCAO = 3
DELAY_BASE_TRADUCAO = 2.0

# Scores do langdetect são evidência, não garantia de acerto. Textos curtos
# também precisam de conteúdo suficiente, mesmo quando o score é alto.
CONFIANCA_MINIMA = 0.95
MARGEM_MINIMA = 0.25
MIN_LETRAS_LATINAS = 20
MIN_PALAVRAS_LATINAS = 4

# Placeholder para entidades que NÃO devem ser traduzidas (@usuarios e emojis).
# Antes de traduzir, trocamos essas entidades pelo placeholder; traduzimos só o
# texto real; depois devolvemos as entidades aos seus devidos lugares.
_PLACEHOLDER_PREFIX = "TKZRT"
_PLACEHOLDER_SUFFIX = "X"
_PLACEHOLDER_RE = re.compile(rf"{_PLACEHOLDER_PREFIX}(\d+){_PLACEHOLDER_SUFFIX}")

# Nomes em português para os códigos ISO 639-1 mais comuns.
NOMES_IDIOMAS = {
    "en": "inglês",
    "en-us": "inglês",
    "es": "espanhol",
    "fr": "francês",
    "de": "alemão",
    "it": "italiano",
    "ja": "japonês",
    "ko": "coreano",
    "zh": "chinês",
    "zh-cn": "chinês",
    "ru": "russo",
    "ar": "árabe",
    "hi": "hindi",
    "pt": "português",
    "pt-br": "português",
    "pt-pt": "português",
    "nl": "holandês",
    "pl": "polonês",
    "tr": "turco",
    "sv": "sueco",
    "no": "norueguês",
    "da": "dinamarquês",
    "fi": "finlandês",
    "cs": "tcheco",
    "el": "grego",
    "he": "hebraico",
    "id": "indonésio",
    "th": "tailandês",
    "vi": "vietnamita",
    "ro": "romeno",
    "hu": "húngaro",
    "uk": "ucraniano",
    "tl": "tagalo",
    "af": "africâner",
    "sw": "suaíli",
    "fa": "persa",
}


def nome_idioma(codigo: str | None) -> str:
    """Resolve um código ISO 639-1 para um nome em português (com fallback)."""
    if not codigo:
        return "outro idioma"
    codigo_norm = codigo.lower()
    if codigo_norm in NOMES_IDIOMAS:
        return NOMES_IDIOMAS[codigo_norm]
    base = codigo_norm.split("-")[0]
    if base in NOMES_IDIOMAS:
        return NOMES_IDIOMAS[base]
    return codigo


def _normalizar_idioma(codigo: str | None) -> str | None:
    if not isinstance(codigo, str) or not codigo.strip():
        return None
    codigo = codigo.strip().lower().replace("_", "-")
    return codigo.split("-")[0]


def _detectar_idioma(texto: str) -> str | None:
    """Detecta apenas linguagem natural com evidência suficiente; abstém na dúvida."""
    texto = _texto_para_deteccao(texto)
    letras = [c for c in texto if c.isalpha()]
    if not letras:
        return None
    # Não contar bytes/ASCII: japonês, chinês etc. também têm letras e não
    # necessariamente usam espaços entre palavras.
    tem_escrita_nao_latina = any(ord(c) > 0x024F for c in letras)
    if tem_escrita_nao_latina:
        if len(letras) < 6:
            return None
    elif len(letras) < MIN_LETRAS_LATINAS or len(texto.split()) < MIN_PALAVRAS_LATINAS:
        return None

    try:
        # Caixa alta é frequente em tweets em PT; não é evidência de alemão.
        candidatos = sorted(detect_langs(texto.casefold()), key=lambda item: item.prob, reverse=True)
        if not candidatos:
            return None
        melhor = candidatos[0]
        segundo = candidatos[1].prob if len(candidatos) > 1 else 0.0
        if melhor.prob < CONFIANCA_MINIMA or melhor.prob - segundo < MARGEM_MINIMA:
            return None
        # Uma alternativa plausível em PT/EN deve preservar o original.
        if any(c.lang in IDIOMAS_SEM_TRADUCAO and c.prob >= 0.05 for c in candidatos[1:]):
            return None
        return melhor.lang
    except Exception as e:
        log.debug("Falha ao detectar idioma: %s", e)
        return None


def _parece_erro_traducao(traducao: str) -> bool:
    """Detecta se a resposta da API é uma página de erro e não uma tradução.

    O deep-translator às vezes retorna o corpo de uma página HTTP de erro
    (ex: "Error 500 (Server Error)!!...") sem lançar exceção. Nesse caso a
    resposta não deve ser tratada como tradução válida.
    """
    if not traducao or not traducao.strip():
        return True
    return bool(_PADRAO_ERRO_TRADUCAO.search(traducao))


_RISO_PATTERN = (
    r"(?<!\w)(?:l?k{3,}|(?:ha|he|hi|hu){3,}|(?:ah){3,}|"
    r"(?:ja){3,}|(?:rs){2,})(?!\w)"
)
_EMOJI_BASE = "[\u2600-\u27BF\U0001F000-\U0001FAFF]"
_EMOJI_PARTE = _EMOJI_BASE + "[\ufe0e\ufe0f]?[\U0001F3FB-\U0001F3FF]?"
_ENTIDADE_RE = re.compile(
    r"https?://[^\s<>]+|www\.[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"@[\w._]+|#[\w]+|" + _RISO_PATTERN + "|"
    r"[0-9#*]\ufe0f?\u20e3|" + _EMOJI_PARTE + "(?:\u200d" + _EMOJI_PARTE + ")*",
    re.UNICODE | re.IGNORECASE,
)


def _texto_para_deteccao(texto: str) -> str:
    """Remove entidades, sem inserir códigos artificiais no detector."""
    texto = _ENTIDADE_RE.sub(" ", texto or "")
    return " ".join(re.findall(r"[^\W\d_]+", texto, re.UNICODE))


def _eh_ruido_laughing(texto: str) -> bool:
    """Ignora risadas/símbolos sem descartar frases de alfabetos não latinos."""
    if not texto:
        return False
    letras = "".join(c for c in _texto_para_deteccao(texto) if c.isalpha())
    if not letras:
        # Sem nenhuma letra (só símbolos/emojis) -> nada a traduzir de fato.
        return True
    return len(letras) >= 4 and len(set(letras.casefold())) == 1


def _isolar_entidades(texto: str) -> tuple[str, dict]:
    """Protege links, hashtags, menções, risadas e emojis durante a tradução.

    Retorna (texto_com_placeholders, {idx: original}). O texto retornado tem
    todo o conteúdo traduzível preservado; as entidades são trocadas por
    placeholders seguros que o tradutor não altera.
    """
    originais: dict[int, str] = {}
    contador = [0]

    def _repl(match: re.Match) -> str:
        idx = contador[0]
        contador[0] += 1
        originais[idx] = match.group(0)
        return f"{_PLACEHOLDER_PREFIX}{idx}{_PLACEHOLDER_SUFFIX}"

    return _ENTIDADE_RE.sub(_repl, texto), originais


def _restaurar_entidades(texto: str, originais: dict) -> str:
    """Devolve as entidades (por índice) aos lugares dos placeholders."""
    if not originais or not texto:
        return texto

    def _repl(match: re.Match) -> str:
        idx = int(match.group(1))
        return originais.get(idx, match.group(0))

    return _PLACEHOLDER_RE.sub(_repl, texto)


def traduzir_com_detalhes(
    texto: str, alvo: str = "pt", *, idioma_informado: str | None = None,
) -> dict:
    """Traduz o texto para o idioma alvo se ele não estiver em português.

    Retorna um dict:
      - 'original': o texto original (inalterado)
      - 'traduzido': o texto traduzido, ou None se não houve tradução
      - 'idioma_origem': código ISO do idioma de origem (ou None)
      - 'foi_traduzido': bool indicando se foi traduzido

    Não traduz quando:
      - O texto já estiver em português (idioma alvo);
      - O texto estiver em inglês (mantém o original);
      - A detecção falhar ou for incerta;
      - A tradução falhar ou retornar uma página de erro da API.

    O idioma informado pela fonte protege PT/EN e precisa concordar com o
    detector para outros idiomas. "und"/"zxx" não autorizam uma tradução.
    """
    resultado = {
        "original": texto,
        "traduzido": None,
        "idioma_origem": None,
        "foi_traduzido": False,
    }
    if not texto or not texto.strip():
        return resultado
    if GoogleTranslator is None:  # pragma: no cover
        return resultado

    texto_limpo = texto.strip()
    idioma_fonte = _normalizar_idioma(idioma_informado)
    if idioma_fonte in (*IDIOMAS_SEM_TRADUCAO, "und", "zxx", _normalizar_idioma(alvo)):
        return resultado

    # Regra de ruído: risadas/repetições (hahahah, lkkkkkk) não devem ser
    # traduzidas -- o tradutor inventa um significado errado.
    if _eh_ruido_laughing(texto_limpo):
        log.debug(f"Tradução ignorada: texto parece ruído ({texto_limpo[:50]}...)")
        return resultado

    idioma = _detectar_idioma(_texto_para_deteccao(texto_limpo))
    if not idioma:
        # Detecção incerta -> não arrisca traduzir conteúdo já em PT.
        return resultado
    if _normalizar_idioma(idioma) in (*IDIOMAS_SEM_TRADUCAO, _normalizar_idioma(alvo)):
        # PT (já é o alvo) e EN (usuário quer manter original) não são traduzidos.
        return resultado
    if idioma_fonte and idioma_fonte != _normalizar_idioma(idioma):
        return resultado

    # Só a requisição de tradução recebe placeholders; o detector nunca os vê.
    # Evita colisão caso o usuário tenha escrito um desses códigos literalmente.
    if _PLACEHOLDER_RE.search(texto_limpo):
        return resultado
    texto_isolado, entidades = _isolar_entidades(texto_limpo)
    tokens_esperados = Counter(_PLACEHOLDER_RE.findall(texto_isolado))

    try:
        origem_tradutor = {"zh-cn": "zh-CN", "zh-tw": "zh-TW", "he": "iw"}.get(idioma, idioma)
        tradutor = GoogleTranslator(source=origem_tradutor, target=alvo)
        for tentativa in range(1, MAX_TENTATIVAS_TRADUCAO + 1):
            try:
                traducao = tradutor.translate(texto_isolado)
            except Exception as e:
                log.warning(f"Falha na tradução automática (tentativa {tentativa}/{MAX_TENTATIVAS_TRADUCAO}): {e}")
                traducao = None

            if traducao and not _parece_erro_traducao(traducao):
                if Counter(_PLACEHOLDER_RE.findall(traducao)) == tokens_esperados:
                    restaurado = _restaurar_entidades(traducao, entidades)
                    if restaurado.strip() == texto_limpo:
                        return resultado
                    resultado["traduzido"] = restaurado
                    resultado["idioma_origem"] = idioma
                    resultado["foi_traduzido"] = True
                    break
                log.debug("Tradução descartada: entidades protegidas foram alteradas")
            if tentativa < MAX_TENTATIVAS_TRADUCAO:
                # Backoff exponencial: 2s, 4s ...
                espera = DELAY_BASE_TRADUCAO * (2 ** (tentativa - 1))
                log.info(f"Tradução indisponível, nova tentativa em {espera:.0f}s...")
                time.sleep(espera)
    except Exception as e:
        log.warning(f"Erro inesperado na tradução automática: {e}")
    return resultado


def traduzir_se_necessario(
    texto: str, alvo: str = "pt", *, idioma_informado: str | None = None,
) -> str:
    """Versão simplificada: retorna apenas o texto traduzido (ou o original)."""
    detalhes = traduzir_com_detalhes(texto, alvo=alvo, idioma_informado=idioma_informado)
    if detalhes["foi_traduzido"]:
        return detalhes["traduzido"]
    return texto
