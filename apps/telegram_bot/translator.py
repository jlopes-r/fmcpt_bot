import logging

from langdetect import detect, DetectorFactory, LangDetectException

try:
    from deep_translator import GoogleTranslator
except Exception:  # pragma: no cover
    GoogleTranslator = None

log = logging.getLogger(__name__)

# Detecção determinística entre processos
DetectorFactory.seed = 0

IDIOMAS_PT_PREFIX = ("pt",)


def _detectar_idioma(texto: str) -> str | None:
    """Detecta o idioma do texto. Retorna None se não for possível."""
    if not texto or not texto.strip():
        return None
    try:
        return detect(texto)
    except (LangDetectException, Exception) as e:
        log.debug(f"Falha ao detectar idioma: {e}")
        return None


def traduzir_se_necessario(texto: str, alvo: str = "pt") -> str:
    """Traduz o texto para o idioma alvo se ele não estiver em português.

    Retorna o texto original quando:
      - O texto já estiver em português;
      - A detecção falhar ou for incerta (evita traduzir conteúdo PT à toa);
      - A tradução em si falhar.
    """
    if not texto or not texto.strip():
        return texto
    if GoogleTranslator is None:  # pragma: no cover
        return texto

    texto_limpo = texto.strip()
    idioma = _detectar_idioma(texto_limpo)
    if idioma:
        if idioma.startswith(IDIOMAS_PT_PREFIX):
            return texto
    else:
        # Detecção incerta -> não arrisca traduzir conteúdo já em PT.
        return texto

    try:
        tradutor = GoogleTranslator(source="auto", target=alvo)
        traducao = tradutor.translate(texto_limpo)
        if traducao:
            return traducao
    except Exception as e:
        log.warning(f"Falha na tradução automática: {e}")
    return texto
