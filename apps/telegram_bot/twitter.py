import re

from apps.telegram_bot.translator import nome_idioma, traduzir_com_detalhes


TWEET_URL_RE = re.compile(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)")
PROFILE_URL_RE = re.compile(r"(?:x|twitter)\.com/([^/]+)/?$")


def traduzir_texto_tweet(tweet: dict) -> str:
    """Traduz só o texto deste tweet, respeitando o idioma da própria fonte."""
    detalhes = traduzir_com_detalhes(
        tweet.get("text") or "",
        idioma_informado=tweet.get("lang") or tweet.get("language"),
    )
    if detalhes["foi_traduzido"]:
        return (
            f"{detalhes['traduzido']}\n\n---\n"
            f"🔎 Traduzido do {nome_idioma(detalhes['idioma_origem'])}"
        )
    return detalhes["original"]


def match_tweet_url(url: str):
    return TWEET_URL_RE.search(url)


def match_profile_url(url: str):
    """Extrai o username quando a URL aponta para um perfil do X/Twitter.

    Ignora paths de status, lists, hashtags e intents. Retorna o match ou None."""
    parsed = re.sub(r'[?#].*$', '', url.strip())
    match = PROFILE_URL_RE.search(parsed)
    if not match:
        return None
    user = match.group(1)
    # Confirma que o primeiro segmento é realmente um perfil (não /status, /i/, etc.)
    if user in ("status", "i", "hashtag", "search", "explore", "settings", "home", "notifications", "favorites", "lists", "messages"):
        return None
    # Evita pegar coisas do tipo /intent/, /share/, /compose/
    if any(seg in user for seg in ("intent", "share", "compose", "search", "about", "en", "pt")):
        return None
    return match


def build_follow_info_url(username: str) -> str:
    """Endpoint público e sem auth do Twitter que reporta conta protegida."""
    return f"https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={username}"


def build_profile_url(username: str) -> str:
    """Endpoint vxtwitter para dados de perfil (sem /status)."""
    return f"https://api.vxtwitter.com/{username}"


def build_vxtwitter_url(username: str, status_id: str) -> str:
    return f"https://api.vxtwitter.com/{username}/status/{status_id}"


def build_fxtwitter_url(username: str, status_id: str) -> str:
    return f"https://api.fxtwitter.com/{username}/status/{status_id}"
