import re
import time
import logging

from langdetect import detect, DetectorFactory, LangDetectException

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

# Regra de "ruído" (risadas tipo "hahahahah", "lkkkkkkkk", "ahahhah").
# Se o texto tiver poucas letras distintas do alfabeto (é basicamente letra
# repetida) e for longo o bastante, não traduzimos: o tradutor desvirtua isso.
RUIDO_MAX_LETRAS_DISTINTAS = 5   # cobre até 5 letras do alfabeto distintas
RUIDO_MIN_TAMANHO = 6            # tamanho mínimo para encarar como ruído longo

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


def _detectar_idioma(texto: str) -> str | None:
    """Detecta o idioma do texto. Retorna None se não for possível."""
    if not texto or not texto.strip():
        return None
    try:
        return detect(texto)
    except (LangDetectException, Exception) as e:
        log.debug(f"Falha ao detectar idioma: {e}")
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


# Regex unificada que captura qualquer entidade a ser preservada na tradução:
# @usuários (com ponto e underscore) ou emojis/pictogramas/símbolos.
_ENTIDADE_RE = re.compile(
    r"(@[\w._]+)|(?:"
    "["
    "\U00002700-\U000027BF"
    "\U0001F000-\U0001F6FF"
    "\U0001F780-\U0001F7FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0001F1E6-\U0001F1FF"
    "]+)",
    re.UNICODE,
)


def _eh_ruido_laughing(texto: str) -> bool:
    """True se o texto for "ruído" (risadas/repetições) e não deva ser traduzido.

    Um texto é considerado ruído quando tem poucas letras distintas do alfabeto
    (ex: "hahahahah", "lkkkkkkkk") e comprimento suficiente. Nesse caso o
    tradutor inventa uma tradução errada, então mantemos o original.
    """
    if not texto:
        return False
    letras = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", texto)
    if not letras:
        # Sem nenhuma letra (só símbolos/emojis) -> nada a traduzir de fato.
        return True
    distintas = set(letras.lower())
    return (
        len(texto) >= RUIDO_MIN_TAMANHO
        and len(distintas) <= RUIDO_MAX_LETRAS_DISTINTAS
    )


def _isolar_entidades(texto: str) -> tuple[str, dict]:
    """Substitui @usuarios e emojis por placeholders, guardando os originais.

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


def traduzir_com_detalhes(texto: str, alvo: str = "pt") -> dict:
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

    # Regra de ruído: risadas/repetições (hahahah, lkkkkkk) não devem ser
    # traduzidas -- o tradutor inventa um significado errado.
    if _eh_ruido_laughing(texto_limpo):
        log.debug(f"Tradução ignorada: texto parece ruído ({texto_limpo[:50]}...)")
        return resultado

    # Isola @usuarios e emojis para traduzir só o texto real; depois de traduzir
    # reinserimos as entidades nos seus devidos lugares.
    texto_isolado, entidades = _isolar_entidades(texto_limpo)

    idioma = _detectar_idioma(texto_isolado)
    if not idioma:
        # Detecção incerta -> não arrisca traduzir conteúdo já em PT.
        return resultado
    if idioma.startswith(IDIOMAS_SEM_TRADUCAO):
        # PT (já é o alvo) e EN (usuário quer manter original) não são traduzidos.
        return resultado

    try:
        tradutor = GoogleTranslator(source="auto", target=alvo)
        for tentativa in range(1, MAX_TENTATIVAS_TRADUCAO + 1):
            try:
                traducao = tradutor.translate(texto_isolado)
            except Exception as e:
                log.warning(f"Falha na tradução automática (tentativa {tentativa}/{MAX_TENTATIVAS_TRADUCAO}): {e}")
                traducao = None

            if traducao and not _parece_erro_traducao(traducao):
                resultado["traduzido"] = _restaurar_entidades(traducao, entidades)
                resultado["idioma_origem"] = idioma
                resultado["foi_traduzido"] = True
                break
            if tentativa < MAX_TENTATIVAS_TRADUCAO:
                # Backoff exponencial: 2s, 4s ...
                espera = DELAY_BASE_TRADUCAO * (2 ** (tentativa - 1))
                log.info(f"Tradução indisponível, nova tentativa em {espera:.0f}s...")
                time.sleep(espera)
    except Exception as e:
        log.warning(f"Erro inesperado na tradução automática: {e}")
    return resultado


def traduzir_se_necessario(texto: str, alvo: str = "pt") -> str:
    """Versão simplificada: retorna apenas o texto traduzido (ou o original)."""
    detalhes = traduzir_com_detalhes(texto, alvo=alvo)
    if detalhes["foi_traduzido"]:
        return detalhes["traduzido"]
    return texto
