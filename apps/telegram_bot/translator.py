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
    idioma = _detectar_idioma(texto_limpo)
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
                traducao = tradutor.translate(texto_limpo)
            except Exception as e:
                log.warning(f"Falha na tradução automática (tentativa {tentativa}/{MAX_TENTATIVAS_TRADUCAO}): {e}")
                traducao = None

            if traducao and not _parece_erro_traducao(traducao):
                resultado["traduzido"] = traducao
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
