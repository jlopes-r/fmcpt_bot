"""
Instagram Extractor v2 — Reescrito do zero.

Pipeline de extração de 4 camadas, sem APIs externas:
  1. API Interna do Instagram (i.instagram.com/api/v1)
  2. GraphQL com doc_id público
  3. Embed Page Scraping (__additionalDataLoaded / _sharedData)
  4. yt-dlp com cookies (fallback para vídeos/reels)

Não depende de: iGram, SaveIG, SnapInsta, Cobalt, RapidAPI.
"""
import re
import os
import json
import time
import asyncio
import logging
import urllib.parse
import http.cookiejar
from datetime import datetime
from functools import partial

import yt_dlp
import httpx

log = logging.getLogger("SuperBot")

# ─── Regex ────────────────────────────────────────────────────────────────────
SHORTCODE_REGEX = re.compile(r'/(?:p|reel|reels|ad|tv)/([A-Za-z0-9_-]+)')
STORIES_REGEX = re.compile(r'/stories/([^/]+)/([0-9]+)')

# ─── Headers que imitam um navegador real ─────────────────────────────────────
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.instagram.com',
    'Referer': 'https://www.instagram.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
}

IG_APP_HEADERS = {
    'X-IG-App-ID': '936619743392459',
    'X-ASBD-ID': '198387',
    'X-IG-WWW-Claim': '0',
}

# ─── Estado global de cookies ─────────────────────────────────────────────────
_cookies_known_bad = False
_cookies_bad_since: float = 0.0
_cookies_bad_reason: str = ""
_COOKIES_BAD_RESET_SECONDS = 1800  # 30 min — depois tenta de novo


def cookies_are_valid() -> bool:
    """Verifica se os cookies estão marcados como válidos.
    Reseta automaticamente após 30 minutos para re-testar."""
    global _cookies_known_bad, _cookies_bad_since, _cookies_bad_reason
    if not _cookies_known_bad:
        return True
    elapsed = time.time() - _cookies_bad_since
    if elapsed > _COOKIES_BAD_RESET_SECONDS:
        log.info("🔄 Reset automático de _cookies_known_bad após %.0f min", elapsed / 60)
        _cookies_known_bad = False
        _cookies_bad_since = 0.0
        _cookies_bad_reason = ""
        return True
    return False


def _mark_cookies_bad(reason: str = "") -> None:
    """Marca cookies como inválidos para evitar retentativas inúteis."""
    global _cookies_known_bad, _cookies_bad_since, _cookies_bad_reason
    if not _cookies_known_bad:
        _cookies_known_bad = True
        _cookies_bad_since = time.time()
        _cookies_bad_reason = reason or "Cookies expirados ou inválidos"
        log.warning("🍪❌ Cookies marcados como INVÁLIDOS: %s", _cookies_bad_reason)


def get_cookie_failure_reason() -> str:
    """Retorna o motivo exato pelo qual os cookies falharam."""
    global _cookies_bad_reason
    return _cookies_bad_reason or "Cookies expirados ou login necessário"


def reset_cookies_bad() -> None:
    """Reset manual (chamado quando novos cookies são carregados)."""
    global _cookies_known_bad, _cookies_bad_since, _cookies_bad_reason
    _cookies_known_bad = False
    _cookies_bad_since = 0.0
    _cookies_bad_reason = ""


def _is_challenge_response(resp) -> bool:
    """Detecta se a resposta foi redirecionada para uma página de challenge/login.
    Também reconhece as mensagens JSON que a API interna retorna quando a sessão
    foi bloqueada/exigida (checkpoint_required, login_required, challenge_required)."""
    final_url = str(resp.url)
    if '/challenge/' in final_url or '/accounts/login/' in final_url:
        return True
    body_start = resp.text[:3000].lower() if hasattr(resp, 'text') else ''
    if any(kw in body_start for kw in [
        '/accounts/login/',
        'checkpoint_required',
        'challenge_required',
        'login_required',
        'id="loginform"',
    ]):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Utilidades
# ═══════════════════════════════════════════════════════════════════════════════


def _get_shortcode(url: str) -> str | None:
    """Extrai o shortcode do Instagram da URL."""
    match = SHORTCODE_REGEX.search(url)
    return match.group(1) if match else None


def _is_story(url: str) -> bool:
    """Verifica se a URL é de um story do Instagram."""
    return bool(STORIES_REGEX.search(url))


def _is_reel(url: str) -> bool:
    """Verifica se a URL é de um Reel do Instagram."""
    return bool(re.search(r'/(?:reel|reels)/[A-Za-z0-9_-]+', url))


def _get_embed_path(url: str) -> str:
    """Retorna o tipo de caminho correto para a página de embed."""
    return 'reel' if _is_reel(url) else 'p'


def _get_story_info(url: str) -> tuple[str, str] | None:
    """Extrai username e media_id de uma URL de story.
    O ID numérico no URL do story JÁ é o media_id."""
    match = STORIES_REGEX.search(url)
    if match:
        return match.group(1), match.group(2)
    return None


def get_profile_username(url: str) -> str | None:
    """Extrai username quando a URL aponta para um perfil do Instagram."""
    try:
        parsed = urllib.parse.urlparse(url)
        path_parts = [part for part in parsed.path.strip('/').split('/') if part]
    except Exception:
        return None

    if len(path_parts) != 1:
        return None

    username = path_parts[0]
    if username in {'p', 'reel', 'reels', 'tv', 'ad', 'stories', 'explore', 'accounts'}:
        return None
    if not re.fullmatch(r'[A-Za-z0-9._]{1,30}', username):
        return None
    return username


def _sanitize_caption(text: str) -> str:
    """Limpa a caption removendo caracteres problemáticos."""
    if not text:
        return ''
    try:
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        return text.strip()
    except Exception:
        return ''


def _shortcode_to_media_id(shortcode: str) -> str:
    """Converte shortcode do Instagram para media_id numérico."""
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    media_id = 0
    for char in shortcode:
        if char in alphabet:
            media_id = media_id * 64 + alphabet.index(char)
    return str(media_id)


def _load_cookies_from_file(cookie_path: str) -> dict:
    """Carrega cookies do arquivo Netscape e retorna um dict nome→valor.
    Também valida se sessionid existe e não está expirado."""
    cookies = {}
    if not cookie_path or not os.path.exists(cookie_path):
        return cookies
    try:
        cj = http.cookiejar.MozillaCookieJar(cookie_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        for cookie in cj:
            cookies[cookie.name] = cookie.value
        log.info("🍪 Cookies carregados: %s", ', '.join(cookies.keys()))

        # ── Validação de saúde dos cookies ──
        if 'sessionid' not in cookies:
            log.warning("⚠️ Cookies carregados mas SEM sessionid — autenticação não vai funcionar")
        else:
            for cookie in cj:
                if cookie.name == 'sessionid' and cookie.expires:
                    now = time.time()
                    if cookie.expires < now:
                        dias_expirado = (now - cookie.expires) / 86400
                        log.warning(
                            "⚠️ sessionid EXPIRADO há %.1f dias (expirou em %s)",
                            dias_expirado,
                            datetime.fromtimestamp(cookie.expires).strftime('%Y-%m-%d %H:%M')
                        )
                    else:
                        dias_restantes = (cookie.expires - now) / 86400
                        log.info(
                            "✅ sessionid válido por mais %.1f dias (expira em %s)",
                            dias_restantes,
                            datetime.fromtimestamp(cookie.expires).strftime('%Y-%m-%d %H:%M')
                        )
                    break
    except Exception as e:
        log.warning("Falha ao carregar cookies: %s", str(e)[:100])
    return cookies


def inspect_cookie_health(cookie_path: str) -> str:
    """Gera um relatório legível sobre o estado dos cookies do Instagram.

    Lê o arquivo Netscape na hora, verifica se o sessionid existe e se está
    expirado, e retorna quantos dias faltam para expirar (ou há quanto expirou).
    """
    if not cookie_path or not os.path.exists(cookie_path):
        return "❌ Arquivo de cookies não encontrado em:\n`%s`" % cookie_path

    try:
        cj = http.cookiejar.MozillaCookieJar(cookie_path)
        cj.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        return "❌ Falha ao ler o arquivo de cookies: %s" % str(e)[:200]

    total = len(cj)
    nomes = sorted(cookie.name for cookie in cj)
    tem_sessionid = any(cookie.name == 'sessionid' for cookie in cj)

    linhas = [f"🍪 **Cookies do Instagram**", ""]
    linhas.append(f"📄 Arquivo: `{cookie_path}`")
    linhas.append(f"🔢 Cookies: `{total}`")
    if nomes:
        linhas.append(f"🧩 Campos: `{', '.join(nomes)}`")

    sessionid = None
    for cookie in cj:
        if cookie.name == 'sessionid':
            sessionid = cookie
            break

    linhas.append("")
    if not tem_sessionid:
        linhas.append("⚠️ **Sem `sessionid`** — a autenticação não vai funcionar.")
        linhas.append("💡 Use `/ig_renew` para gerar cookies novos.")
    elif sessionid.expires:
        now = time.time()
        if sessionid.expires < now:
            dias = (now - sessionid.expires) / 86400
            linhas.append(
                "❌ **sessionid EXPIRADO** há %.1f dias (expirou em %s).\n"
                "💡 Use `/ig_renew` para gerar cookies novos." % (
                    dias,
                    datetime.fromtimestamp(sessionid.expires).strftime('%Y-%m-%d %H:%M'),
                )
            )
        else:
            dias = (sessionid.expires - now) / 86400
            linhas.append(
                "✅ **sessionid VÁLIDO** por mais %.1f dias (expira em %s)." % (
                    dias,
                    datetime.fromtimestamp(sessionid.expires).strftime('%Y-%m-%d %H:%M'),
                )
            )
    else:
        linhas.append("ℹ️ `sessionid` presente, mas sem data de expiração registrada.")

    linhas.append("")
    linhas.append("Status global em memória: " + ("⚠️ cookies marcados como INVALIDOS" if _cookies_known_bad else "✅ ok"))
    return "\n".join(linhas)


async def validate_cookie_health(cookie_path: str) -> dict:
    """Confere a validade REAL dos cookies chamando um endpoint autenticado da API
    web do Instagram (news/inbox). Ele só retorna 200 com dados para quem está
    logado — diferente do /api/v1/media que responde mesmo deslogado.

    Retorna:
      {'valid': True}                                                    OU
      {'valid': False, 'reason': '...'}
    """
    if not cookie_path or not os.path.exists(cookie_path):
        return {"valid": False, "reason": "Arquivo de cookies não encontrado"}

    cookies = _load_cookies_from_file(cookie_path)
    if not cookies or 'sessionid' not in cookies:
        return {"valid": False, "reason": "Arquivo de cookies sem `sessionid`"}

    headers = {
        **BROWSER_HEADERS,
        **IG_APP_HEADERS,
        'X-Requested-With': 'XMLHttpRequest',
        'Cookie': _build_cookie_header(cookies),
    }
    csrf = cookies.get('csrftoken')
    if csrf:
        headers['X-CSRFToken'] = csrf

    api_url = 'https://www.instagram.com/api/v1/news/inbox/'
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            log.info("🍪 Validação real do cookie: status=%d", resp.status_code)
            text = resp.text or ''
            body_low = text[:3000].lower()

            # Sinais claros de sessão inválida / bloqueio
            if _is_challenge_response(resp) or any(
                kw in body_low for kw in ('login_required', 'checkpoint_required', 'challenge_required')
            ):
                _mark_cookies_bad("Validação real: sessão rejeitada pelo Instagram")
                return {"valid": False, "reason": "Instagram exigiu login/verificação — sessão inválida"}

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except (json.JSONDecodeError, ValueError):
                    return {"valid": False, "reason": "Resposta não-JSON — possível bloqueio"}

                if any(k in data for k in ('counts', 'new_stories', 'old_stories')):
                    reset_cookies_bad()  # endpoint autenticado respondeu ⇒ sessão está valendo
                    return {'valid': True, 'reason': ''}

                return {"valid": False, "reason": f"Resposta inesperada: {text[:120]}"}

            if resp.status_code in (400, 401, 403):
                _mark_cookies_bad("Validação real: status %d do Instagram" % resp.status_code)
                return {"valid": False, "reason": f"Instagram rejeitou a sessão (status {resp.status_code})"}

            # 429 = rate-limit transitório — não marca cookies como ruins
            return {"valid": False, "reason": f"Instagram limitou requisições (status {resp.status_code}) — tente de novo em instantes"}
    except Exception as e:
        log.info("❌ Validação real falhou: %s", str(e)[:150])
        return {"valid": False, "reason": f"Erro ao validar cookies: {str(e)[:120]}"}


def _build_cookie_header(cookies: dict) -> str:
    """Monta a string Cookie: para o header HTTP."""
    return '; '.join(f'{k}={v}' for k, v in cookies.items())


def _parse_profile_user(user: dict) -> dict | None:
    if not user:
        return None
    return {
        'username': user.get('username') or '',
        'full_name': _sanitize_caption(user.get('full_name') or ''),
        'biography': _sanitize_caption(user.get('biography') or ''),
        'followers': user.get('edge_followed_by', {}).get('count'),
        'following': user.get('edge_follow', {}).get('count'),
        'posts': user.get('edge_owner_to_timeline_media', {}).get('count'),
        'is_private': bool(user.get('is_private')),
        'is_verified': bool(user.get('is_verified')),
        'profile_pic_url': user.get('profile_pic_url_hd') or user.get('profile_pic_url') or '',
        'external_url': user.get('external_url') or '',
    }


def _parse_profile_from_html(html: str) -> dict | None:
    patterns = [
        r'"user"\s*:\s*({.+?})\s*,\s*"logging_page_id"',
        r'"ProfilePage"\s*,\s*\[\s*({.+?})\s*\]',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except Exception:
            continue
        user = data.get('graphql', {}).get('user') if isinstance(data, dict) else None
        result = _parse_profile_user(user or data)
        if result and result.get('username'):
            return result
    return None


async def fetch_instagram_profile(url: str, cookie_path: str = '') -> dict | None:
    """Busca dados públicos de um perfil do Instagram."""
    username = get_profile_username(url)
    if not username:
        return None

    cookies = _load_cookies_from_file(cookie_path)
    headers = {
        **BROWSER_HEADERS,
        **IG_APP_HEADERS,
        'X-Requested-With': 'XMLHttpRequest',
    }
    if cookies:
        headers['Cookie'] = _build_cookie_header(cookies)

    api_url = f'https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(username)}'
    page_url = f'https://www.instagram.com/{urllib.parse.quote(username)}/'

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            log.info("👤 Instagram perfil API @%s status=%d", username, resp.status_code)
            if resp.status_code == 200:
                user = resp.json().get('data', {}).get('user')
                result = _parse_profile_user(user)
                if result and result.get('username'):
                    return result

            resp = await client.get(page_url, headers=headers)
            log.info("👤 Instagram perfil HTML @%s status=%d", username, resp.status_code)
            if resp.status_code == 200:
                return _parse_profile_from_html(resp.text)
    except Exception as e:
        log.info("❌ Falha ao buscar perfil Instagram @%s: %s", username, str(e)[:150])

    return None


def _auto_login_and_save_cookies(cookie_path: str) -> dict:
    """
    Faz login no Instagram via Instaloader usando IG_USERNAME/IG_PASSWORD do .env.
    Gera cookies frescos a partir do IP da VM e salva no arquivo.
    Retorna o dict de cookies ou {} se falhar.
    """
    username = os.getenv('IG_USERNAME', '').strip()
    password = os.getenv('IG_PASSWORD', '').strip()

    if not username or not password:
        log.info("🔑 IG_USERNAME/IG_PASSWORD não configurados no .env, pulando auto-login")
        return {}

    log.info("🔐 Tentando auto-login no Instagram como '%s'...", username)

    try:
        import instaloader
        L = instaloader.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_videos=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
        )

        L.login(username, password)
        log.info("✅ Login no Instagram bem-sucedido!")

        # Extrai cookies da sessão e salva no formato Netscape
        session = L.context._session
        os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

        cj = http.cookiejar.MozillaCookieJar(cookie_path)
        for cookie in session.cookies:
            cj.set_cookie(cookie)
        cj.save(ignore_discard=True, ignore_expires=True)

        log.info("💾 Cookies frescos salvos em: %s", cookie_path)

        # Retorna como dict
        cookies = {}
        for cookie in cj:
            cookies[cookie.name] = cookie.value
        return cookies

    except Exception as e:
        log.warning("❌ Auto-login falhou: %s", str(e)[:200])
        log.warning("   Verifique IG_USERNAME/IG_PASSWORD no .env. "
                     "Se a conta tem 2FA, desative temporariamente ou use uma conta sem 2FA.")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
#  Parsers — transformam dados brutos do IG em nosso formato padrão
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_api_item(item: dict) -> dict | None:
    """Converte um item do formato API (v1) para o nosso dict padrão."""
    urls = []

    # Caption
    caption_obj = item.get('caption', {})
    caption = caption_obj.get('text', '') if isinstance(caption_obj, dict) else str(caption_obj or '')
    caption = _sanitize_caption(caption)

    uploader = (item.get('user') or {}).get('username', 'Autor')

    # Carrossel
    carousel = item.get('carousel_media', [])
    if carousel:
        for m in carousel:
            if m.get('video_versions'):
                urls.append(m['video_versions'][0]['url'])
            elif m.get('image_versions2', {}).get('candidates'):
                urls.append(m['image_versions2']['candidates'][0]['url'])

    # Vídeo único
    elif item.get('video_versions'):
        urls.append(item['video_versions'][0]['url'])

    # Foto única
    elif item.get('image_versions2', {}).get('candidates'):
        urls.append(item['image_versions2']['candidates'][0]['url'])

    if not urls:
        return None

    media_type = 'carousel' if len(urls) > 1 else \
                 'video' if (item.get('video_versions') or any(m.get('video_versions') for m in carousel)) else 'photo'

    return {
        'urls': urls,
        'type': media_type,
        'title': caption,
        'uploader': uploader,
    }


def _parse_graphql_media(media: dict) -> dict | None:
    """Converte um item do formato GraphQL para o nosso dict padrão."""
    urls = []

    # Caption
    edges = media.get('edge_media_to_caption', {}).get('edges', [])
    caption = edges[0].get('node', {}).get('text', '') if edges else ''
    caption = _sanitize_caption(caption)

    uploader = (media.get('owner') or {}).get('username', 'Autor')

    # Carrossel (sidecar)
    sidecar = media.get('edge_sidecar_to_children', {}).get('edges', [])
    if sidecar:
        for edge in sidecar:
            node = edge.get('node', {})
            if node.get('is_video') and node.get('video_url'):
                urls.append(node['video_url'])
            elif node.get('display_url'):
                urls.append(node['display_url'])

    # Vídeo único
    elif media.get('is_video') and media.get('video_url'):
        urls.append(media['video_url'])

    # Foto única
    elif media.get('display_url'):
        urls.append(media['display_url'])

    if not urls:
        return None

    media_type = 'carousel' if len(urls) > 1 else 'video' if media.get('is_video') else 'photo'

    return {
        'urls': urls,
        'type': media_type,
        'title': caption,
        'uploader': uploader,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Camada 1 — API Interna do Instagram (i.instagram.com)
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_via_api(shortcode: str, cookies: dict = None) -> dict | None:
    """
    Usa a API interna do Instagram: /api/v1/media/{media_id}/info/
    Tenta primeiro o endpoint web (www.instagram.com — combina com os headers
    de navegador desktop e com o X-IG-App-ID web) e depois o do app (i.instagram.com).
    Com cookies de sessão válidos, funciona de qualquer IP.
    """
    cookies = cookies or {}
    media_id = _shortcode_to_media_id(shortcode)
    log.info("🔌 Camada 1 (API Interna): shortcode=%s → media_id=%s", shortcode, media_id)

    api_urls = [
        f'https://www.instagram.com/api/v1/media/{media_id}/info/',
        f'https://i.instagram.com/api/v1/media/{media_id}/info/',
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Monta headers com cookies de autenticação
            csrf = cookies.get('csrftoken', '')
            headers = {
                **BROWSER_HEADERS,
                **IG_APP_HEADERS,
                'X-Requested-With': 'XMLHttpRequest',
            }
            if csrf:
                headers['X-CSRFToken'] = csrf
            if cookies:
                headers['Cookie'] = _build_cookie_header(cookies)

            for api_url in api_urls:
                host = api_url.split('//')[1].split('/')[0]
                resp = await client.get(api_url, headers=headers)
                log.info("   API resp status: %d (%s)", resp.status_code, host)

                # ── Detectar challenge/login redirect ──
                if _is_challenge_response(resp):
                    log.warning("   🍪 API (%s) redirecionou para challenge — cookies expirados", host)
                    _mark_cookies_bad("API Interna retornou challenge")
                    return None

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        log.warning("   🍪 API (%s) retornou 200 mas body não é JSON — provável challenge", host)
                        _mark_cookies_bad("API Interna retornou HTML ao invés de JSON")
                        return None
                    items = data.get('items', [])
                    if items:
                        result = _parse_api_item(items[0])
                        if result:
                            log.info("   ✅ API (%s) retornou %d URLs", host, len(result['urls']))
                            return result
                    log.info("   API (%s) retornou JSON mas sem itens válidos", host)
                elif resp.status_code in (400, 401, 403, 429):
                    body = (resp.text or '')[:300].lower()
                    if any(kw in body for kw in ('checkpoint_required', 'login_required', 'challenge_required')):
                        log.warning("   🍪 API (%s) retornou %d com login/challenge — marcando cookies ruins", host, resp.status_code)
                        _mark_cookies_bad("API retornou %d com login/challenge" % resp.status_code)
                        return None
                    log.info("   API (%s) retornou %d: %s", host, resp.status_code, resp.text[:100])
                else:
                    log.info("   API (%s) retornou %d: %s", host, resp.status_code, resp.text[:100])

    except Exception as e:
        log.info("   ❌ API Interna falhou: %s", str(e)[:150])

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Camada 2 — GraphQL com doc_id público
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_via_graphql(shortcode: str, cookies: dict = None) -> dict | None:
    """
    Usa o endpoint GraphQL com o doc_id mais recente + cookies de autenticação.
    Pode quebrar se o Instagram rotacionar o doc_id, mas é fácil de atualizar.
    """
    cookies = cookies or {}
    log.info("🔌 Camada 2 (GraphQL): shortcode=%s", shortcode)

    variables = json.dumps({
        'shortcode': shortcode,
        'child_comment_count': 0,
        'fetch_comment_count': 0,
        'parent_comment_count': 0,
        'has_threaded_comments': False,
    })

    # Lista de doc_ids conhecidos (o mais recente primeiro)
    doc_ids = [
        '8845758582119845',  # doc_id do parth-dl (2025)
        '17991233890457762',  # doc_id antigo (backup)
    ]

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            csrf = cookies.get('csrftoken', '')
            headers = {
                **BROWSER_HEADERS,
                **IG_APP_HEADERS,
                'X-CSRFToken': csrf,
                'X-Requested-With': 'XMLHttpRequest',
            }
            if cookies:
                headers['Cookie'] = _build_cookie_header(cookies)

            for doc_id in doc_ids:
                query_url = (
                    f"https://www.instagram.com/graphql/query/"
                    f"?doc_id={doc_id}"
                    f"&variables={urllib.parse.quote(variables)}"
                )

                resp = await client.get(query_url, headers=headers)
                log.info("   GraphQL doc_id=%s → status=%d", doc_id, resp.status_code)

                # ── Detectar challenge/login redirect ──
                if _is_challenge_response(resp):
                    log.warning("   🍪 GraphQL redirecionou para challenge — cookies expirados")
                    _mark_cookies_bad("GraphQL retornou challenge")
                    return None

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        log.warning("   🍪 GraphQL retornou 200 mas body não é JSON — provável challenge")
                        _mark_cookies_bad("GraphQL retornou HTML ao invés de JSON")
                        return None
                    # Formato novo (xdt_shortcode_media)
                    data_obj = data.get('data') or {}
                    media = data_obj.get('xdt_shortcode_media') or data_obj.get('shortcode_media')

                    if media:
                        result = _parse_graphql_media(media)
                        if result:
                            log.info("   ✅ GraphQL retornou %d URLs", len(result['urls']))
                            return result
                    log.info("   GraphQL retornou JSON mas sem media válida")

    except Exception as e:
        log.info("   ❌ GraphQL falhou: %s", str(e)[:150])

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Camada 3 — Embed Page Scraping
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_via_embed(shortcode: str, cookies: dict = None, embed_path: str = 'p') -> dict | None:
    """
    Faz scraping da página de embed do Instagram.
    Procura por __additionalDataLoaded, _sharedData, ou tags meta OG.
    """
    log.info("🔌 Camada 3 (Embed Scraping): shortcode=%s", shortcode)
    embed_url = f'https://www.instagram.com/{embed_path}/{shortcode}/embed/'

    cookies = cookies or {}
    try:
        embed_headers = {**BROWSER_HEADERS}
        if cookies:
            embed_headers['Cookie'] = _build_cookie_header(cookies)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=embed_headers)
            log.info("   Embed status: %d, body length: %d", resp.status_code, len(resp.text))

            # ── Detectar challenge/login redirect ──
            if _is_challenge_response(resp):
                log.warning("   🍪 Embed redirecionou para challenge — cookies expirados")
                _mark_cookies_bad("Embed retornou challenge")
                return None

            if resp.status_code != 200:
                log.info("   Embed retornou %d (provavelmente redirect para login)", resp.status_code)
                return None

            html = resp.text

            # Método 1: __additionalDataLoaded (formato moderno)
            match = re.search(
                r'window\.__additionalDataLoaded\s*\(\s*[^,]+,\s*({.+?})\s*\)',
                html, re.DOTALL
            )
            if match:
                log.info("   Encontrou __additionalDataLoaded")
                data = json.loads(match.group(1))

                # Formato items (API-like)
                items = data.get('items', [])
                if items:
                    result = _parse_api_item(items[0])
                    if result:
                        log.info("   ✅ Embed (additionalData/items) → %d URLs", len(result['urls']))
                        return result

                # Formato GraphQL
                gql_media = data.get('graphql', {}).get('shortcode_media') or data.get('shortcode_media')
                if gql_media:
                    result = _parse_graphql_media(gql_media)
                    if result:
                        log.info("   ✅ Embed (additionalData/graphql) → %d URLs", len(result['urls']))
                        return result

            # Método 2: _sharedData (formato antigo)
            match = re.search(
                r'window\._sharedData\s*=\s*({.+?});\s*</script>',
                html, re.DOTALL
            )
            if match:
                log.info("   Encontrou _sharedData")
                data = json.loads(match.group(1))
                post_page = data.get('entry_data', {}).get('PostPage', [{}])[0]
                media = post_page.get('graphql', {}).get('shortcode_media')
                if media:
                    result = _parse_graphql_media(media)
                    if result:
                        log.info("   ✅ Embed (_sharedData) → %d URLs", len(result['urls']))
                        return result

            # Método 3: Extrair do HTML puro (og:image, display_url, video_url)
            urls_found = []

            # display_url / video_url no JSON inline
            for pattern in [
                r'"video_url"\s*:\s*"([^"]+)"',
                r'"display_url"\s*:\s*"([^"]+)"',
            ]:
                for m in re.finditer(pattern, html):
                    raw_url = m.group(1).replace('\\u0026', '&').replace('\\/', '/')
                    if raw_url not in urls_found:
                        urls_found.append(raw_url)

            # og:image / og:video nas meta tags
            for pattern in [
                r'<meta\s+property="og:video"\s+content="([^"]+)"',
                r'<meta\s+property="og:image"\s+content="([^"]+)"',
            ]:
                for m in re.finditer(pattern, html):
                    raw_url = m.group(1).replace('&amp;', '&')
                    if raw_url not in urls_found:
                        urls_found.append(raw_url)

            # EmbeddedMediaImage / EmbeddedVideoPlayer
            for pattern in [
                r'class="EmbeddedVideoPlayer"[^>]*src="([^"]+)"',
                r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"',
            ]:
                for m in re.finditer(pattern, html):
                    raw_url = m.group(1).replace('&amp;', '&')
                    if raw_url not in urls_found:
                        urls_found.append(raw_url)

            if urls_found:
                # Tenta extrair caption
                caption = ''
                caption_match = re.search(r'"caption"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
                if caption_match:
                    try:
                        caption = _sanitize_caption(
                            caption_match.group(1).encode().decode('unicode_escape')
                        )
                    except Exception:
                        caption = _sanitize_caption(caption_match.group(1))

                has_video = any('video' in u.lower() or '.mp4' in u.lower() for u in urls_found)
                log.info("   ✅ Embed (HTML scraping) → %d URLs", len(urls_found))
                return {
                    'urls': urls_found,
                    'type': 'carousel' if len(urls_found) > 1 else 'video' if has_video else 'photo',
                    'title': caption,
                    'uploader': 'Autor',
                }

            log.info("   Embed não encontrou nenhum URL de mídia")

    except Exception as e:
        log.info("   ❌ Embed falhou: %s", str(e)[:150])

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Camada 4 — yt-dlp (força bruta)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_ytdlp(url: str, ydl_opts: dict) -> dict:
    """Executa yt-dlp em thread separada."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def _extract_via_ytdlp(url: str, cookie_path: str, out_dir: str) -> dict | None:
    """
    Usa yt-dlp com ou sem cookies para baixar vídeos/reels.
    Não funciona para fotos (retorna 'No video formats found').
    """
    log.info("🔌 Camada 4 (yt-dlp): %s", url)

    loop = asyncio.get_running_loop()
    ydl_opts = {
        'outtmpl': os.path.join(out_dir, '%(id)s_%(index)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': False,
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 2,
        'http_headers': {
            'User-Agent': BROWSER_HEADERS['User-Agent'],
        },
    }

    # Adiciona cookies se existirem
    if cookie_path and os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path

    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, partial(_run_ytdlp, url, ydl_opts)),
            timeout=60
        )

        entries = info.get('entries', [info])
        arquivos = []

        for item in entries:
            path = None
            if 'requested_downloads' in item:
                for dl in item['requested_downloads']:
                    if 'filepath' in dl and os.path.exists(dl['filepath']):
                        path = dl['filepath']
                        break
            if not path:
                path = item.get('filepath')
                if path and os.path.exists(path):
                    arquivos.append(path)
            elif path not in arquivos:
                arquivos.append(path)

        if not arquivos:
            return None

        log.info("   ✅ yt-dlp baixou %d arquivo(s)", len(arquivos))
        return {
            'type': 'carousel' if len(arquivos) > 1 else (
                'video' if arquivos[0].endswith(('.mp4', '.mov', '.webm')) else 'photo'
            ),
            'files': arquivos,
            'title': _sanitize_caption(info.get('title') or info.get('description') or ''),
            'uploader': info.get('uploader') or info.get('channel') or 'Autor',
        }

    except asyncio.TimeoutError:
        log.info("   ⏰ yt-dlp timeout")
        return None
    except Exception as e:
        log.warning("   ❌ yt-dlp falhou: %s", str(e)[:300])
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Orquestrador Principal
# ═══════════════════════════════════════════════════════════════════════════════

async def download_instagram(
    url: str,
    cookie_path: str,
    out_dir: str
) -> dict | None:
    """
    Pipeline completo de download do Instagram v2.

    Ordem de tentativa:
      1. API Interna (i.instagram.com) — funciona p/ tudo, sem login
      2. GraphQL (doc_id público) — bom para fotos/carrossel
      3. Embed Scraping — extrai do HTML da página embed
      4. yt-dlp — força bruta, bom para Reels/vídeos

    Retorna dict com:
      - urls: lista de URLs diretas da CDN, OU
      - files: lista de caminhos locais (quando yt-dlp baixa)
      - type: 'photo' | 'video' | 'carousel'
      - title: caption/legenda
      - uploader: nome do autor
      - _cookies_failed: True se falhou por cookies expirados (para o caller)
    """
    log.info("📷 Instagram Extractor v2: %s", url)
    shortcode = _get_shortcode(url)

    # Carrega cookies para autenticar as requisições
    cookies = _load_cookies_from_file(cookie_path)

    # ── BUG ANTERIOR: quando _cookies_known_bad era True, pulávamos as camadas 1-3
    #    direto pro yt-dlp. Mas os cookies são relidos do disco a cada chamada e o
    #    sessionid pode estar válido — o flag costuma ser gravado por um rate-limit
    #    temporário do Instagram, não por cookies expirados. Então SEMPRE tentamos
    #    as camadas 1-3 com os cookies atuais antes de cair pro yt-dlp.
    if not cookies_are_valid():
        log.info(
            "⚠️ Cookies marcados como inválidos antes (%s) — tentando camadas 1-3 mesmo assim",
            get_cookie_failure_reason(),
        )

    # Stories: tenta API primeiro (story_id = media_id), depois yt-dlp
    if _is_story(url):
        story_info = _get_story_info(url)
        if story_info:
            username, story_media_id = story_info
            log.info("📖 Story detectado: @%s, media_id=%s", username, story_media_id)

            # Camada 1: API Interna (funciona para stories com cookies válidos)
            if cookies:
                try:
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                        csrf = cookies.get('csrftoken', '')
                        api_url = f'https://i.instagram.com/api/v1/media/{story_media_id}/info/'
                        headers = {
                            **BROWSER_HEADERS,
                            **IG_APP_HEADERS,
                        }
                        if csrf:
                            headers['X-CSRFToken'] = csrf
                        headers['Cookie'] = _build_cookie_header(cookies)

                        resp = await client.get(api_url, headers=headers)
                        log.info("   Story API status: %d", resp.status_code)

                        # Detectar challenge
                        if _is_challenge_response(resp):
                            log.warning("   🍪 Story API redirecionou para challenge")
                            _mark_cookies_bad("Story API retornou challenge")
                        elif resp.status_code == 200:
                            try:
                                data = resp.json()
                            except (json.JSONDecodeError, ValueError):
                                log.warning("   🍪 Story API body não é JSON")
                                _mark_cookies_bad("Story API retornou HTML")
                                data = None
                            if data:
                                items = data.get('items', [])
                                if items:
                                    result = _parse_api_item(items[0])
                                    if result:
                                        log.info("   ✅ Story via API: %d URLs", len(result['urls']))
                                        reset_cookies_bad()  # sessão funcionou — flag era falso positivo
                                        return result
                                log.info("   API retornou 200 mas sem itens válidos para story")
                        else:
                            log.info("   Story API retornou %d", resp.status_code)
                except Exception as e:
                    log.info("   ❌ Story API falhou: %s", str(e)[:200])

            # Camada 2: yt-dlp (fallback)
            log.info("   Tentando yt-dlp para story...")
            result = await _extract_via_ytdlp(url, cookie_path, out_dir)
            if result:
                return result

        log.warning("❌ Todas as tentativas falharam para Story: %s", url)
        return None

    if not shortcode:
        log.warning("❌ Não foi possível extrair shortcode de: %s", url)
        return None

    # ── Tentativa 1: com cookies existentes ──
    result = await _try_all_layers(shortcode, cookies, url)
    if result:
        reset_cookies_bad()  # as camadas funcionaram — cookies estão OK
        return result

    # ── Se challenge foi detectado nas camadas acima, não tenta auto-login ──
    if _cookies_known_bad:
        log.info("⏩ Challenge detectado — pulando auto-login, direto para yt-dlp")
    elif os.getenv('IG_USERNAME') and os.getenv('IG_PASSWORD'):
        # ── Tentativa 2: auto-login para gerar cookies frescos ──
        log.info("🔄 Cookies falharam. Tentando auto-login para gerar cookies frescos...")
        loop = asyncio.get_running_loop()
        fresh_cookies = await loop.run_in_executor(
            None, _auto_login_and_save_cookies, cookie_path
        )
        if fresh_cookies:
            reset_cookies_bad()  # Auto-login gerou cookies novos
            result = await _try_all_layers(shortcode, fresh_cookies, url)
            if result:
                reset_cookies_bad()
                return result
    else:
        log.info("🔑 Auto-login não disponível (IG_USERNAME/IG_PASSWORD não configurados)")

    # ── Camada Final: yt-dlp (força bruta) ──
    result = await _extract_via_ytdlp(url, cookie_path, out_dir)
    if result:
        log.info("✅ Instagram download via yt-dlp: %s", url)
        return result

    log.warning("❌ Todas as tentativas falharam para: %s", url)
    return None


def _is_acceptable_result_for_url(result: dict | None, url: str) -> bool:
    """Evita tratar thumbnail de Reel como extração final."""
    if not result:
        return False
    if _is_reel(url) and result.get('type') == 'photo':
        log.info("⏭️ Resultado de Reel veio como foto; ignorando thumbnail e tentando fallback de vídeo")
        return False
    return True


async def _try_all_layers(shortcode: str, cookies: dict, url: str) -> dict | None:
    """Tenta as 3 camadas de extração (API, GraphQL, Embed) com os cookies fornecidos."""

    # ── Camada 1: API Interna ──
    result = await _extract_via_api(shortcode, cookies)
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via API Interna: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ API Interna falhou, tentando Camada 2...")

    # ── Camada 2: GraphQL ──
    result = await _extract_via_graphql(shortcode, cookies)
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via GraphQL: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ GraphQL falhou, tentando Camada 3...")

    # ── Camada 3: Embed Scraping ──
    result = await _extract_via_embed(shortcode, cookies, _get_embed_path(url))
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via Embed: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ Embed falhou...")

    return None
