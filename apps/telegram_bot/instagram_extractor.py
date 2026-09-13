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
import tempfile
from contextvars import ContextVar
from html.parser import HTMLParser
from datetime import datetime

import httpx

from apps.telegram_bot.downloaders import baixar_com_ytdlp
from apps.telegram_bot.instagram_resilience import (
    InstagramAccountPool,
    InstagramFailure,
    classify_instagram_response,
    load_graphql_documents,
    resolve_instagram_share_url,
)

log = logging.getLogger("SuperBot")

# ─── Regex ────────────────────────────────────────────────────────────────────
SHORTCODE_REGEX = re.compile(r'/(?:p|reel|reels|ad|tv)/([A-Za-z0-9_-]+)')
STORIES_REGEX = re.compile(r'/stories/(?!highlights(?:/|$))([^/?#]+)(?:/([0-9]+))?')
HIGHLIGHTS_REGEX = re.compile(r'/stories/highlights/([0-9]+)')


def _bounded_env_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


_instagram_account_pool = InstagramAccountPool(
    failure_threshold=int(_bounded_env_number('IG_CIRCUIT_FAILURES', 2, 1, 10)),
    circuit_seconds=_bounded_env_number('IG_CIRCUIT_SECONDS', 300, 10, 3600),
    invalid_cookie_seconds=_bounded_env_number(
        'IG_INVALID_COOKIE_COOLDOWN', 1800, 60, 86400
    ),
    challenge_seconds=_bounded_env_number('IG_CHALLENGE_COOLDOWN', 3600, 60, 86400),
    ip_rate_limit_seconds=_bounded_env_number('IG_429_COOLDOWN', 120, 10, 3600),
)
_active_ig_account: ContextVar[str | None] = ContextVar(
    'active_instagram_account', default=None
)
_active_ig_endpoint: ContextVar[str] = ContextVar(
    'active_instagram_endpoint', default='unknown'
)

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

# ─── Rate-limit (429) do Instagram ───────────────────────────────────────────
# Quando o Instagram devolve 429 (throttle por IP), disparar varias requisicoes
# em rajada so piora. Guardamos quando o ultimo 429 aconteceu e damos um
# cooldown para dar espaco entre tentativas.
_ig_429_since: float = 0.0
_IG_429_COOLDOWN = 120.0         # segundos de respeito apos um 429
_IG_PROFILE_PACING = 2.0         # espaco entre API -> HTML -> oembed

# ─── Pacing global de requisicoes web ao Instagram ─────────────────────────
# Rajadas de dezenas de requests por segundo para www/i.instagram.com sao o
# principal sinal de "automacao" e derrubam a conta em challenge/checkpoint.
# Espacamos as chamadas web num ritmo proximo do humano e serializamos as
# concorrentes para o bot nunca disparar em rajada.
_IG_MIN_REQUEST_INTERVAL = 1.3   # segundos minimos entre dois requests web do IG
_ig_last_request_at: float = 0.0


async def _ig_wait_pacing() -> None:
    """Garante um gap minimo global entre requisicoes web ao Instagram.

    Reserva um slot futuro imediatamente, entao chamadas concorrentes se
    espaçam entre si sem rajada. Um unico sleep (sem loop) — seguro mesmo
    quando asyncio.sleep esta mockado em testes.
    """
    global _ig_last_request_at
    now = time.time()
    target = max(_ig_last_request_at + _IG_MIN_REQUEST_INTERVAL, now)
    _ig_last_request_at = target + _IG_MIN_REQUEST_INTERVAL
    delay = target - now
    if delay > 0:
        await asyncio.sleep(delay)


def ig_429_cooldown_remaining() -> float:
    """Segundos restantes do cooldown global de 429 (0.0 se inativo)."""
    if not _ig_429_recente():
        return 0.0
    return max(0.0, _ig_429_since + _IG_429_COOLDOWN - time.time())


async def aguardar_cooldown_429(max_wait: float = 45.0) -> float:
    """Dorme o necessario para sair do cooldown de 429 (limitado a max_wait).

    Retorna quantos segundos foram aguardados (0.0 se nao havia cooldown).
    """
    restante = ig_429_cooldown_remaining()
    if restante > 0:
        await asyncio.sleep(min(restante, max_wait))
    return restante

# ─── Cache de perfil ──────────────────────────────────────────────────────────
# Buscar o perfil no Instagram toda hora (web_profile_info) enche o rate-limit
# (429). Guardamos o ultimo perfil valido buscado por N minutos em memoria e em
# disco, e so re-buscamos se passar do TTL. Serve tambem de fallback quando um
# 429 interrompe uma nova busca.
_IG_PROFILE_CACHE_TTL = 3600.0   # 1h
_profile_cache_ttl: dict = {}    # username -> expiry timestamp
_profile_cache: dict = {}        # username -> profile dict

if os.environ.get("IG_DATA_DIR"):
    _profile_cache_path = os.path.join(os.environ["IG_DATA_DIR"], "profile_cache.json")
else:
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _profile_cache_path = os.path.join(_project_root, "data", "profile_cache.json")


def _profile_cache_load() -> None:
    """Carrega o cache de disco para a memoria (username -> (expiry, profile))."""
    global _profile_cache, _profile_cache_ttl
    try:
        if os.path.exists(_profile_cache_path):
            with open(_profile_cache_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            now = time.time()
            _profile_cache = {}
            _profile_cache_ttl = {}
            for username, entry in (raw.get("profiles") or {}).items():
                expiry = float(entry.get("expiry") or 0)
                if expiry > now:
                    _profile_cache[username] = entry.get("profile")
                    _profile_cache_ttl[username] = expiry
    except Exception as e:
        log.info("⚠️ Nao conseguiu carregar cache de perfil: %s", str(e)[:120])


def _profile_cache_save() -> None:
    """Persiste o cache de perfil em disco."""
    try:
        payload = {
            "profiles": {
                username: {"expiry": exp, "profile": _profile_cache.get(username)}
                for username, exp in _profile_cache_ttl.items()
            },
            "saved_at": time.time(),
        }
        tmp = _profile_cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, _profile_cache_path)
    except Exception as e:
        log.info("⚠️ Nao conseguiu salvar cache de perfil: %s", str(e)[:120])


def _profile_cache_get(username: str) -> dict | None:
    """Retorna o perfil cacheado (se valido) ou None."""
    exp = _profile_cache_ttl.get(username)
    if not exp:
        return None
    if time.time() > exp:
        _profile_cache.pop(username, None)
        _profile_cache_ttl.pop(username, None)
        return None
    return _profile_cache.get(username)


def _profile_cache_store(username: str, profile: dict) -> None:
    """Guarda/renova o perfil cacheado."""
    _profile_cache[username] = profile
    _profile_cache_ttl[username] = time.time() + _IG_PROFILE_CACHE_TTL


def _profile_cache_upsert_privacy(username: str, is_private: bool) -> None:
    """Grava is_private no cache (cria entrada minima se nao existir)."""
    existing = _profile_cache.get(username)
    if existing and isinstance(existing, dict) and existing.get('is_private') is not None:
        return
    profile = dict(existing) if isinstance(existing, dict) else {'username': username, 'partial': True}
    profile['is_private'] = is_private
    _profile_cache_store(username, profile)


def _profile_cache_clear() -> None:
    """Limpa o cache em memoria e no disco."""
    global _profile_cache, _profile_cache_ttl
    _profile_cache = {}
    _profile_cache_ttl = {}
    try:
        if os.path.exists(_profile_cache_path):
            os.remove(_profile_cache_path)
    except Exception:
        pass


_profile_cache_load()

# ─── Fim cache de perfil ──────────────────────────────────────────────────────


def _mark_ig_429() -> None:
    """Registra o momento do ultimo 429 para cooldown global."""
    global _ig_429_since
    _ig_429_since = time.time()
    account_id = _active_ig_account.get()
    if account_id:
        _instagram_account_pool.report_failure(
            account_id,
            _active_ig_endpoint.get(),
            InstagramFailure.IP_RATE_LIMITED,
            "Instagram retornou status 429",
        )


def _ig_429_recente() -> bool:
    """True se houve 429 nos ultimos segundos (para nem tentar rajada)."""
    return bool(_ig_429_since) and (time.time() - _ig_429_since) < _IG_429_COOLDOWN


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


def _mark_cookies_bad(
    reason: str = "",
    failure: InstagramFailure | None = None,
) -> None:
    """Marca cookies como inválidos para evitar retentativas inúteis."""
    global _cookies_known_bad, _cookies_bad_since, _cookies_bad_reason
    account_id = _active_ig_account.get()
    if account_id:
        failure = failure or (
            InstagramFailure.CHALLENGE
            if any(word in reason.casefold() for word in ('challenge', 'checkpoint'))
            else InstagramFailure.COOKIE_INVALID
        )
        _instagram_account_pool.report_failure(
            account_id,
            _active_ig_endpoint.get(),
            failure,
            reason or "Sessao rejeitada ou verificacao exigida",
        )
        return
    if not _cookies_known_bad:
        _cookies_known_bad = True
        _cookies_bad_since = time.time()
        _cookies_bad_reason = reason or "Sessao rejeitada ou verificacao exigida"
        log.warning("🍪❌ Cookies marcados como INVÁLIDOS: %s", _cookies_bad_reason)


def get_cookie_failure_reason() -> str:
    """Retorna o motivo exato pelo qual os cookies falharam."""
    global _cookies_bad_reason
    return _cookies_bad_reason or "Sessao rejeitada ou login/verificacao necessarios"


def reset_cookies_bad(*, reset_pool: bool = True) -> None:
    """Reset manual (chamado quando novos cookies são carregados)."""
    global _cookies_known_bad, _cookies_bad_since, _cookies_bad_reason
    _cookies_known_bad = False
    _cookies_bad_since = 0.0
    _cookies_bad_reason = ""
    account_id = _active_ig_account.get()
    endpoint = _active_ig_endpoint.get()
    if account_id and endpoint not in {'unknown', 'ytdlp'}:
        _instagram_account_pool.report_success(account_id, endpoint)
    elif not account_id and reset_pool:
        _instagram_account_pool.reset()


def get_instagram_account_health() -> dict[str, object]:
    """Retorna um snapshot da saude individual das contas e do IP."""
    return _instagram_account_pool.snapshot()


async def _run_account_endpoint(
    account_id: str,
    endpoint: str,
    operation,
    *args,
):
    """Executa uma camada somente quando seu circuit breaker permite."""
    if account_id and not _instagram_account_pool.can_attempt(account_id, endpoint):
        log.info(
            "Instagram circuit aberto: conta=%s endpoint=%s",
            _instagram_account_pool.health(account_id).label,
            endpoint,
        )
        return None
    account_token = _active_ig_account.set(account_id or None)
    endpoint_token = _active_ig_endpoint.set(endpoint)
    try:
        result = await operation(*args)
        is_success = bool(result) and not (
            isinstance(result, dict) and result.get('valid') is False
        )
        if is_success and account_id:
            _instagram_account_pool.report_success(account_id, endpoint)
        return result
    finally:
        _active_ig_endpoint.reset(endpoint_token)
        _active_ig_account.reset(account_token)


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


def _observe_instagram_response(resp, context: str) -> InstagramFailure | None:
    """Atualiza o estado de saude usando uma classificacao unica de resposta."""
    failure = classify_instagram_response(resp)
    if failure is InstagramFailure.IP_RATE_LIMITED:
        _mark_ig_429()
    elif failure in {InstagramFailure.COOKIE_INVALID, InstagramFailure.CHALLENGE}:
        _mark_cookies_bad(f"{context}: {failure.value}", failure)
    elif failure is InstagramFailure.TRANSIENT:
        account_id = _active_ig_account.get()
        if account_id:
            _instagram_account_pool.report_failure(
                account_id,
                _active_ig_endpoint.get(),
                failure,
                f"{context}: status {getattr(resp, 'status_code', 0)}",
            )
    return failure


# ═══════════════════════════════════════════════════════════════════════════════
#  Utilidades
# ═══════════════════════════════════════════════════════════════════════════════


def _get_shortcode(url: str) -> str | None:
    """Extrai o shortcode do Instagram da URL."""
    match = SHORTCODE_REGEX.search(url)
    return match.group(1) if match else None


def _is_story(url: str) -> bool:
    """Verifica se a URL é de um story do Instagram (não destaque)."""
    if HIGHLIGHTS_REGEX.search(url):
        return False
    return bool(STORIES_REGEX.search(url))


def _is_highlight(url: str) -> bool:
    """Verifica se a URL é de um destaque (highlights) do Instagram."""
    return bool(HIGHLIGHTS_REGEX.search(url))


def _get_highlight_id(url: str) -> str | None:
    """Extrai o ID numérico de um destaque do Instagram."""
    match = HIGHLIGHTS_REGEX.search(url)
    return match.group(1) if match else None


def _is_reel(url: str) -> bool:
    """Verifica se a URL é de um Reel do Instagram."""
    return bool(re.search(r'/(?:reel|reels)/[A-Za-z0-9_-]+', url))


def _get_embed_path(url: str) -> str:
    """Retorna o tipo de caminho correto para a página de embed."""
    return 'reel' if _is_reel(url) else 'p'


def _get_story_info(url: str) -> tuple[str, str | None] | None:
    """Extrai username e media_id de uma URL de story.
    O ID numérico no URL do story JÁ é o media_id. Links para a
    sequencia atual do perfil podem nao trazer esse ID."""
    match = STORIES_REGEX.search(url)
    if match:
        username = urllib.parse.unquote(match.group(1))
        if re.fullmatch(r'[A-Za-z0-9._]{1,30}', username):
            return username, match.group(2)
    return None


def get_profile_username(url: str) -> str | None:
    """Extrai username quando a URL aponta para um perfil do Instagram."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {'http', 'https'} or (parsed.hostname or '').lower() not in {
            'instagram.com', 'www.instagram.com', 'm.instagram.com',
        }:
            return None
        path_parts = [part for part in parsed.path.strip('/').split('/') if part]
    except Exception:
        return None

    if len(path_parts) != 1:
        return None

    username = path_parts[0]
    if username.lower() in {'p', 'reel', 'reels', 'tv', 'ad', 'stories', 'explore', 'accounts'}:
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


async def _validate_cookie_health_request(cookie_path: str) -> dict:
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

    await _ig_wait_pacing()
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
            failure = classify_instagram_response(resp)

            # Um 429 pode terminar numa URL de login por causa do redirect,
            # mas continua sendo limite temporario do IP, nao prova de sessao
            # invalida. Classifique antes de procurar challenge na URL final.
            if failure is InstagramFailure.IP_RATE_LIMITED:
                _mark_ig_429()
                return {
                    "valid": False,
                    "rate_limited": True,
                    "failure": failure.value,
                    "reason": "Instagram limitou requisicoes (status 429) — tente novamente mais tarde",
                }

            # Sinais claros de sessão inválida / bloqueio
            if failure in {InstagramFailure.COOKIE_INVALID, InstagramFailure.CHALLENGE}:
                _mark_cookies_bad(
                    "Validação real: sessão rejeitada pelo Instagram", failure
                )
                return {
                    "valid": False,
                    "failure": failure.value,
                    "challenge": failure is InstagramFailure.CHALLENGE,
                    "reason": (
                        "Instagram exigiu verificação manual (challenge/checkpoint)"
                        if failure is InstagramFailure.CHALLENGE
                        else "Instagram exigiu login; cookie expirado ou inválido"
                    ),
                }

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
                return {
                    "valid": False,
                    "failure": InstagramFailure.TRANSIENT.value,
                    "reason": f"Instagram rejeitou a requisição (status {resp.status_code})",
                }

            # Outros 5xx/erros transitorios nao invalidam cookies nem o IP.
            _observe_instagram_response(resp, "validacao de cookie")
            return {"valid": False, "reason": f"Instagram respondeu status {resp.status_code} — tente de novo em instantes"}
    except Exception as e:
        log.info("❌ Validação real falhou: %s", str(e)[:150])
        return {"valid": False, "reason": f"Erro ao validar cookies: {str(e)[:120]}"}


async def validate_cookie_health(cookie_path: str) -> dict:
    """Valida uma conta e atualiza apenas a saúde daquele arquivo de cookies."""
    if not cookie_path or not os.path.exists(cookie_path):
        return await _validate_cookie_health_request(cookie_path)
    account_id = _instagram_account_pool.register('health', cookie_path)
    result = await _run_account_endpoint(
        account_id,
        'cookie_health',
        _validate_cookie_health_request,
        cookie_path,
    )
    if result is not None:
        return result
    health = _instagram_account_pool.health(account_id)
    return {
        'valid': False,
        'failure': health.state,
        'reason': health.reason or 'Conta em cooldown; aguarde antes de validar novamente',
        'cooldown': True,
    }


def _build_cookie_header(cookies: dict) -> str:
    """Monta a string Cookie: para o header HTTP."""
    return '; '.join(f'{k}={v}' for k, v in cookies.items())


def _profile_is_complete(profile: dict | None) -> bool:
    """Somente dados conhecidos podem encerrar a busca e usar o cache de 1h."""
    return bool(
        profile and profile.get('username') and profile.get('profile_pic_url')
        and all(profile.get(key) is not None for key in (
            'biography', 'followers', 'following', 'posts', 'is_private',
        ))
    )


def _merge_profiles(primary: dict | None, extra: dict | None) -> dict | None:
    """Completa lacunas sem trocar o perfil ou apagar valores conhecidos (0/False/bio vazia)."""
    if not primary:
        return dict(extra) if extra else None
    result = dict(primary)
    if not extra or str(extra.get('username', '')).lower() != str(primary.get('username', '')).lower():
        return result
    for key, value in extra.items():
        missing = key not in result or result[key] is None
        if key not in {'biography', 'partial'}:
            missing = missing or result.get(key) == ''
        if key != 'partial' and missing and value is not None:
            result[key] = value
    pictures = []
    for profile in (primary, extra):
        for candidate in [profile.get('profile_pic_url'), *(profile.get('profile_pic_urls') or [])]:
            if isinstance(candidate, str) and candidate and candidate not in pictures:
                pictures.append(candidate)
    result['profile_pic_urls'] = pictures
    result['partial'] = not _profile_is_complete(result)
    return result


def _parse_profile_user(user: dict, username: str | None = None) -> dict | None:
    if not isinstance(user, dict) or not isinstance(user.get('username'), str):
        return None
    if not re.fullmatch(r'[A-Za-z0-9._]{1,30}', user['username']):
        return None
    if username and user['username'].lower() != username.lower():
        return None

    def count(edge: str, field: str):
        nested = user.get(edge)
        value = nested.get('count') if isinstance(nested, dict) else None
        return user.get(field) if value is None else value

    bio = user.get('biography')
    if bio is None and isinstance(user.get('biography_with_entities'), dict):
        bio = user['biography_with_entities'].get('raw_text')
    pictures = [user.get('profile_pic_url_hd')]
    hd_info = user.get('hd_profile_pic_url_info')
    if isinstance(hd_info, dict):
        pictures.append(hd_info.get('url'))
    pictures.append(user.get('profile_pic_url'))
    pictures = list(dict.fromkeys(p for p in pictures if isinstance(p, str) and p))
    business_flags = [user.get('is_business_account'), user.get('is_professional_account')]
    result = {
        'username': user['username'],
        'full_name': _sanitize_caption(user.get('full_name') or ''),
        'biography': _sanitize_caption(bio) if isinstance(bio, str) else None,
        'followers': count('edge_followed_by', 'follower_count'),
        'following': count('edge_follow', 'following_count'),
        'posts': count('edge_owner_to_timeline_media', 'media_count'),
        'reels': count('edge_felix_video_timeline', 'clip_metadata_count'),
        'is_private': user.get('is_private'),
        'is_verified': user.get('is_verified'),
        'is_business': any(business_flags) if any(v is not None for v in business_flags) else None,
        'category': user.get('category_name') or user.get('category') or '',
        'profile_pic_url': pictures[0] if pictures else '',
        'profile_pic_urls': pictures,
        'external_url': user.get('external_url') or '',
    }
    result['partial'] = not _profile_is_complete(result)
    return result


class _ProfileHTMLParser(HTMLParser):
    """Lê metadados e scripts como dados; nenhum JavaScript é executado."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.scripts = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = (attrs.get('property') or attrs.get('name') or '').lower()
            self.meta[key] = attrs.get('content') or ''
        elif tag == 'script':
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self._script is not None:
            self.scripts.append(''.join(self._script))
            self._script = None


def _parse_profile_from_html(html: str, username: str | None = None) -> dict | None:
    """Suporta JSON de hydration/Relay e payloads antigos, com identidade conferida."""
    parser = _ProfileHTMLParser()
    parser.feed(html)
    decoder = json.JSONDecoder()
    candidates = []
    for script in parser.scripts:
        # JSON puro, window._sharedData, __additionalDataLoaded e requireLazy.
        # raw_decode respeita chaves aninhadas e aspas dentro da bio.
        starts = [m.start() for m in re.finditer(r'[\[{]', script)]
        consumed = -1
        for start in starts:
            if start < consumed:
                continue
            try:
                payload, consumed = decoder.raw_decode(script, start)
            except (ValueError, RecursionError):
                continue
            pending = [(payload, 0)]
            while pending:
                node, depth = pending.pop()
                if isinstance(node, dict):
                    result = _parse_profile_user(node, username)
                    if result:
                        candidates.append(result)
                    pending.extend((value, depth) for value in node.values() if isinstance(value, (dict, list)))
                    # Alguns blocos de Relay transportam o JSON serializado em __bbox.result.data.
                    pending.extend((value, depth + 1) for value in node.values()
                                   if isinstance(value, str) and depth < 2 and value.lstrip().startswith(('{', '[')))
                elif isinstance(node, list):
                    pending.extend((value, depth) for value in node)
                elif isinstance(node, str) and depth <= 2:
                    try:
                        pending.append((json.loads(node), depth))
                    except (ValueError, RecursionError):
                        pass
    candidates.sort(key=lambda item: sum(item.get(key) is not None and item.get(key) != '' for key in (
        'biography', 'profile_pic_url', 'followers', 'following', 'posts', 'is_private',
    )), reverse=True)
    result = None
    for candidate in candidates:
        result = _merge_profiles(result, candidate)
    return result


def _parse_profile_meta(html: str, username: str) -> dict | None:
    """Recupera a bio de OG sem confundir descrição de login/contadores com bio."""
    parser = _ProfileHTMLParser()
    parser.feed(html)
    meta = parser.meta
    title = meta.get('og:title') or meta.get('twitter:title') or ''
    description = meta.get('og:description') or meta.get('description') or ''
    handles = re.findall(r'\(@([A-Za-z0-9._]+)\)', title)
    if not handles:
        handles = re.findall(r'\(@([A-Za-z0-9._]+)\)\s+(?:on|no)\s+Instagram', description, re.IGNORECASE)
    canonical = meta.get('og:url')
    if canonical:
        canonical_username = get_profile_username(canonical)
        if not canonical_username or canonical_username.lower() != username.lower():
            return None
    if handles and any(handle.lower() != username.lower() for handle in handles):
        return None
    if not handles and not canonical:
        return None  # Instagram/login genérico não identifica o perfil pedido.

    title = re.sub(r'\s*\(@[^)]*\).*$', '', title)
    title = re.sub(r'\s*[|\u2022\u2023-]\s*Instagram.*$', '', title, flags=re.IGNORECASE)
    if title.strip().lower() == 'instagram':
        title = ''
    bio_match = re.search(r'\(@' + re.escape(username) + r'\)\s+(?:on|no)\s+Instagram\s*:\s*(.*)$',
                          description, re.IGNORECASE | re.DOTALL)
    bio = None
    if bio_match:
        bio = bio_match.group(1).strip()
        if len(bio) >= 2 and (bio[0], bio[-1]) in {('"', '"'), ('“', '”'), ("'", "'")}:
            bio = bio[1:-1]
        bio = _sanitize_caption(bio)

    def stat(label: str) -> str | None:
        match = re.search(rf'([\d][\d.,]*(?:\s*(?:mil|[KMB]))?)\s+(?:{label})\b', description, re.IGNORECASE)
        return match.group(1) if match else None

    picture = meta.get('og:image') or meta.get('twitter:image') or ''
    return {
        'username': username,
        'full_name': _sanitize_caption(title.strip()) or username,
        'biography': bio,
        'followers': stat('followers|seguidores'),
        'following': stat('following|seguindo'),
        'posts': stat('posts|publicações|publicacoes'),
        'is_private': None,
        'is_verified': None,
        'is_business': None,
        'category': '',
        'profile_pic_url': picture,
        'profile_pic_urls': [picture] if picture else [],
        'external_url': '',
        'partial': True,
    }


async def fetch_instagram_profile(
    url: str,
    cookie_path: str = '',
    *,
    secondary_cookie_path: str = '',
) -> dict | None:
    """Busca dados públicos de um perfil do Instagram, com cache de 1h.

    Usa cache em memoria/disco primeiro (chave = username). Só bate na API do
    Instagram (web_profile_info / HTML) se o cache expirou. Isso evita
    o 429 de rate-limit que aparecia ao re-buscar o mesmo perfil a cada mensagem.
    Se a chamada de rede falhar ou levar 429 e houver cache, devolve o cache.
    """
    username = get_profile_username(url)
    if not username:
        return None
    username = username.lower()

    # 1) Cache valido? responde na hora, sem tocar no Instagram.
    cached = _profile_cache_get(username)
    if cached and _profile_is_complete(cached) and str(cached.get('username', '')).lower() == username:
        log.info("👤 Instagram perfil @%s (cache)", username)
        return cached

    api_url = f'https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(username)}'
    page_url = f'https://www.instagram.com/{urllib.parse.quote(username)}/'
    cookie_paths = list(dict.fromkeys([cookie_path] + (
        [secondary_cookie_path] if secondary_cookie_path else []
    )))

    result = None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            rate_limited = _ig_429_recente()
            if rate_limited:
                log.info("👤 Instagram perfil @%s: cooldown de 429 ativo — rotas web ignoradas", username)
            else:
                for index, path in enumerate(cookie_paths):
                    cookies = _load_cookies_from_file(path)
                    if index > 0 and not cookies:
                        continue
                    headers = {
                        **BROWSER_HEADERS,
                        **IG_APP_HEADERS,
                        'X-Requested-With': 'XMLHttpRequest',
                    }
                    if cookies:
                        headers['Cookie'] = _build_cookie_header(cookies)

                    slot = 'primary' if index == 0 else 'secondary'
                    account_result = None
                    await _ig_wait_pacing()
                    try:
                        resp = await client.get(api_url, headers=headers)
                        log.info("👤 Instagram perfil API @%s (%s) status=%d", username, slot, resp.status_code)
                        if resp.status_code == 429:
                            _mark_ig_429()
                            rate_limited = True
                        elif resp.status_code == 200:
                            payload = resp.json()
                            data = (payload.get('data') or {}) if isinstance(payload, dict) else {}
                            account_result = _parse_profile_user(data.get('user'), username) if isinstance(data, dict) else None
                    except Exception as e:
                        log.info("👤 Instagram perfil API @%s (%s) indisponível: %s", username, slot, type(e).__name__)

                    # So busca o HTML se a API falhou sem ser por throttling.
                    if not _profile_is_complete(account_result) and not rate_limited:
                        await asyncio.sleep(_IG_PROFILE_PACING)
                        try:
                            resp = await client.get(page_url, headers=headers)
                            log.info("👤 Instagram perfil HTML @%s (%s) status=%d", username, slot, resp.status_code)
                            if resp.status_code == 429:
                                _mark_ig_429()
                                rate_limited = True
                            if resp.status_code == 200:
                                account_result = _merge_profiles(
                                    account_result, _parse_profile_from_html(resp.text, username)
                                )
                                account_result = _merge_profiles(
                                    account_result, _parse_profile_meta(resp.text, username)
                                )
                        except Exception as e:
                            log.info("👤 Instagram perfil HTML @%s (%s) indisponível: %s", username, slot, type(e).__name__)

                    result = _merge_profiles(result, account_result)
                    if _profile_is_complete(result) or rate_limited:
                        break

            if not result or not result.get('profile_pic_url'):
                # oEmbed e o ultimo recurso para o card responder (api.instagram.com
                # e host separado do www; vale tentar mesmo sob cooldown).
                if not rate_limited:
                    await asyncio.sleep(_IG_PROFILE_PACING)
                result = _merge_profiles(result, await _fetch_profile_via_oembed(client, username))
    except Exception as e:
        log.info("❌ Falha ao buscar perfil Instagram @%s: %s", username, str(e)[:150])

    # 3) Sucesso -> atualiza cache. Falha -> reutiliza cache antigo se houver.
    if result and result.get('username'):
        # Metadados OG sao apenas um cartao de contingencia; nao os mantemos por
        # uma hora para que uma proxima tentativa possa recuperar os dados completos.
        result['partial'] = not _profile_is_complete(result)
        if not result['partial']:
            _profile_cache_store(username, result)
            _profile_cache_save()
        return result

    fallback = _profile_cache_get(username)
    if (fallback and str(fallback.get('username', '')).lower() == username
            and any(fallback.get(key) is not None for key in ('followers', 'posts', 'biography'))):
        fallback = dict(fallback, partial=not _profile_is_complete(fallback))
        log.info("👤 Instagram perfil @%s (cache apos falha/429)", username)
        return fallback
    return None


async def _fetch_profile_via_oembed(client: httpx.AsyncClient, username: str) -> dict | None:
    """Tenta enriquecer/perfil via oembed publico (api.instagram.com/oembed).

    Devolve um perfil 'degradado' (sem contagens de posts/seguidores) quando a
    API pesada (web_profile_info) ou a page HTML estao bloqueadas/429 — assim o
    card do perfil ainda responde em vez de falhar com None.
    """
    oembed_url = f'https://api.instagram.com/oembed/?url=https://www.instagram.com/{urllib.parse.quote(username)}/'
    await _ig_wait_pacing()
    try:
        resp = await client.get(oembed_url)
        log.info("👤 Instagram perfil oembed @%s status=%d", username, resp.status_code)
        if resp.status_code == 429:
            _mark_ig_429()
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        author_username = get_profile_username(data.get('author_url') or '')
        if not author_username or author_username.lower() != username.lower():
            return None
        nome = data.get('author_name') or username
        # thumbnail_url/title de oEmbed descrevem uma publicação, não a foto/bio do autor.
        picture = data.get('author_thumbnail_url') or ''
        return {
            'username': username,
            'full_name': _sanitize_caption(nome),
            'biography': None,
            'followers': None,
            'following': None,
            'posts': None,
            'is_private': None,
            'is_verified': None,
            'profile_pic_url': picture,
            'profile_pic_urls': [picture] if picture else [],
            'external_url': '',
            'partial': True,
        }
    except Exception as e:
        log.info("❌ oembed @%s falhou: %s", username, str(e)[:120])
        return None


async def _fetch_post_meta_via_oembed(shortcode: str, embed_path: str = 'p') -> dict | None:
    """Busca caption/autor de um post publico via oEmbed oficial.

    api.instagram.com/oembed responde sem login; o campo 'title' traz a
    descricao do post e 'author_name' o autor. Serve para enriquecer o
    resultado do yt-dlp, que no caminho de fallback muitas vezes nao expoe
    a legenda (vem vazio).
    """
    post_url = f'https://www.instagram.com/{embed_path}/{shortcode}/'
    oembed_url = f'https://api.instagram.com/oembed/?url={urllib.parse.quote(post_url, safe="")}'
    await _ig_wait_pacing()
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(oembed_url)
            log.info("   oEmbed post status=%d", resp.status_code)
            if resp.status_code == 429:
                _mark_ig_429()
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            title = data.get('title') or ''
            return {
                'title': _sanitize_caption(title),
                'uploader': _sanitize_caption(data.get('author_name') or ''),
            }
    except Exception as e:
        log.info("   ❌ oEmbed post falhou: %s", str(e)[:120])
        return None


async def detect_profile_privado(
    url: str,
    cookie_path: str = '',
    *,
    secondary_cookie_path: str = '',
) -> bool | None:
    """Detecta se um perfil do Instagram é privado.

    Retorna True se privado, False se público, e None se não der pra determinar
    (ex.: sem rede, conta inexistente, challenge bloqueando).
    """
    username = get_profile_username(url)
    if not username:
        return None
    username = username.lower()

    # Reaproveita o cache de perfil (evita segunda chamada ao Instagram).
    cached = _profile_cache_get(username)
    if cached and cached.get('is_private') is not None:
        return bool(cached.get('is_private'))

    page_url = f'https://www.instagram.com/{urllib.parse.quote(username)}/'
    cookie_paths = list(dict.fromkeys([cookie_path] + (
        [secondary_cookie_path] if secondary_cookie_path else []
    )))
    if _ig_429_recente():
        return None

    for path in cookie_paths:
        cookies = _load_cookies_from_file(path)
        if path != cookie_paths[0] and not cookies:
            continue
        headers = {
            **BROWSER_HEADERS,
            **IG_APP_HEADERS,
            'X-Requested-With': 'XMLHttpRequest',
        }
        if cookies:
            headers['Cookie'] = _build_cookie_header(cookies)
        try:
            await _ig_wait_pacing()
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(page_url, headers=headers)
            if resp.status_code == 429:
                _mark_ig_429()
                return None
            if _is_challenge_response(resp) or resp.status_code != 200:
                continue
        except Exception as e:
            log.info("⚠️ Falha ao detectar privacidade de @%s: %s", username, str(e)[:120])
            continue

        # A pagina pode conter o usuario logado, autores e perfis sugeridos.
        # Uma flag fora do objeto do perfil pedido nao prova sua privacidade.
        profile = _parse_profile_from_html(resp.text, username)
        privado = profile.get('is_private') if profile else None
        if isinstance(privado, bool):
            _profile_cache_upsert_privacy(username, privado)
            return privado
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

def _validated_carousel_urls(
    urls: list[str],
    *,
    returned_children: int,
    expected_children: object = None,
    has_next_page: bool = False,
) -> tuple[list[str], int] | None:
    """Rejeita carrosseis truncados ou com itens sem mídia utilizável."""
    try:
        expected = int(expected_children) if expected_children is not None else returned_children
    except (TypeError, ValueError):
        expected = returned_children
    expected = max(expected, returned_children)
    unique_urls = list(dict.fromkeys(urls))
    complete = (
        not has_next_page
        and returned_children > 0
        and len(urls) == returned_children
        and len(unique_urls) == returned_children
        and returned_children >= expected
    )
    if not complete:
        log.warning(
            "Carrossel incompleto rejeitado: urls=%d filhos=%d esperado=%d has_next=%s",
            len(unique_urls),
            returned_children,
            expected,
            has_next_page,
        )
        return None
    return unique_urls, expected


def _parse_api_item(item: dict) -> dict | None:
    """Converte um item do formato API (v1) para o nosso dict padrão."""
    urls = []
    expected_count = 1

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
        validated = _validated_carousel_urls(
            urls,
            returned_children=len(carousel),
            expected_children=item.get('carousel_media_count'),
            has_next_page=bool(item.get('more_available') or item.get('has_more_available')),
        )
        if not validated:
            return None
        urls, expected_count = validated

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
        '_expected_items': expected_count if carousel else 1,
        '_complete': True,
    }


def _parse_graphql_media(media: dict) -> dict | None:
    """Converte um item do formato GraphQL para o nosso dict padrão."""
    urls = []
    expected_count = 1

    # Caption
    edges = media.get('edge_media_to_caption', {}).get('edges', [])
    caption = edges[0].get('node', {}).get('text', '') if edges else ''
    caption = _sanitize_caption(caption)

    owner = media.get('owner') or {}
    uploader = owner.get('username', 'Autor')

    # Carrossel (sidecar)
    sidecar = media.get('edge_sidecar_to_children', {}).get('edges', [])
    if sidecar:
        for edge in sidecar:
            node = edge.get('node', {})
            if node.get('is_video') and node.get('video_url'):
                urls.append(node['video_url'])
            elif node.get('display_url'):
                urls.append(node['display_url'])
        sidecar_container = media.get('edge_sidecar_to_children', {})
        validated = _validated_carousel_urls(
            urls,
            returned_children=len(sidecar),
            expected_children=sidecar_container.get('count'),
            has_next_page=bool((sidecar_container.get('page_info') or {}).get('has_next_page')),
        )
        if not validated:
            return None
        urls, expected_count = validated

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
        'media_full_name': owner.get('full_name') or '',
        'media_avatar': owner.get('profile_pic_url') or '',
        '_expected_items': expected_count if sidecar else 1,
        '_complete': True,
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
    await _ig_wait_pacing()

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
                failure = _observe_instagram_response(resp, f"API interna {host}")

                # ── Detectar challenge/login redirect ──
                if failure is InstagramFailure.IP_RATE_LIMITED:
                    log.warning("   ⏳ API (%s) limitada pelo Instagram (429); cookies nao foram invalidados", host)
                    # O limite pode afetar apenas o endpoint web. Ainda dentro
                    # da mesma conta, tenta o endpoint autenticado do app.
                    continue
                if failure in {InstagramFailure.COOKIE_INVALID, InstagramFailure.CHALLENGE}:
                    log.warning("   🍪 API (%s) rejeitou a conta: %s", host, failure.value)
                    return None

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        log.warning("   🍪 API (%s) retornou 200 mas body não é JSON", host)
                        return None
                    items = data.get('items', [])
                    if items:
                        result = _parse_api_item(items[0])
                        if result:
                            log.info("   ✅ API (%s) retornou %d URLs", host, len(result['urls']))
                            return result
                    log.info("   API (%s) retornou JSON mas sem itens válidos", host)
                elif resp.status_code in (400, 401, 403, 429):
                    log.info("   API (%s) retornou %d: %s", host, resp.status_code, resp.text[:100])
                else:
                    log.info("   API (%s) retornou %d: %s", host, resp.status_code, resp.text[:100])

    except Exception as e:
        log.info("   ❌ API Interna falhou: %s", str(e)[:150])

    return None


async def _extract_via_api_media_id(media_id: str, cookies: dict) -> dict | None:
    """Extrai Story pela API autenticada usando o media_id ja conhecido."""
    if not media_id or not cookies:
        return None
    try:
        await _ig_wait_pacing()
        headers = {**BROWSER_HEADERS, **IG_APP_HEADERS}
        csrf = cookies.get('csrftoken', '')
        if csrf:
            headers['X-CSRFToken'] = csrf
        headers['Cookie'] = _build_cookie_header(cookies)
        api_url = f'https://i.instagram.com/api/v1/media/{media_id}/info/'
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
        log.info("   Story API status: %d", resp.status_code)
        failure = _observe_instagram_response(resp, "Story por media_id")
        if failure in {
            InstagramFailure.IP_RATE_LIMITED,
            InstagramFailure.COOKIE_INVALID,
            InstagramFailure.CHALLENGE,
        }:
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get('items') or []
        return _parse_api_item(items[0]) if items else None
    except Exception as e:
        log.info("   Story API falhou: %s", str(e)[:200])
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
    await _ig_wait_pacing()

    variables = json.dumps({
        'shortcode': shortcode,
        'child_comment_count': 0,
        'fetch_comment_count': 0,
        'parent_comment_count': 0,
        'has_threaded_comments': False,
    })

    # Conjunto versionado; IDs podem ser rotacionados por configuração sem
    # alterar código ou executar atualizadores dentro do bot.
    documents = load_graphql_documents()
    doc_ids = documents.legacy
    log.info("   GraphQL docset=%s", documents.version)

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

            # Endpoint Relay atual. O formato e o doc_id acompanham o extrator
            # da versao de yt-dlp instalada; a resposta traz um item API-like
            # dentro de if_not_gated_logged_out.
            modern_headers = {
                **headers,
                'X-FB-Friendly-Name': 'PolarisLoggedOutDesktopWWWPostRootContentQuery',
                'Referer': f'https://www.instagram.com/p/{shortcode}/',
            }
            modern_data = {
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'PolarisLoggedOutDesktopWWWPostRootContentQuery',
                'server_timestamps': 'true',
                'variables': json.dumps(
                    {'media_id': _shortcode_to_media_id(shortcode)},
                    separators=(',', ':'),
                ),
                'doc_id': documents.modern,
            }
            resp = await client.post(
                'https://www.instagram.com/api/graphql',
                headers=modern_headers,
                data=modern_data,
            )
            log.info("   GraphQL Relay atual → status=%d", resp.status_code)
            failure = _observe_instagram_response(resp, "GraphQL Relay")
            if failure in {
                InstagramFailure.IP_RATE_LIMITED,
                InstagramFailure.COOKIE_INVALID,
                InstagramFailure.CHALLENGE,
            }:
                return None
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except (json.JSONDecodeError, ValueError):
                    payload = {}
                media = ((payload.get('data') or {}).get('xig_polaris_media') or {})
                product = media.get('if_not_gated_logged_out') or media
                result = _parse_api_item(product) or _parse_graphql_media(product)
                if result:
                    log.info("   ✅ GraphQL atual retornou %d URLs", len(result['urls']))
                    return result

            for doc_id in doc_ids:
                query_url = (
                    f"https://www.instagram.com/graphql/query/"
                    f"?doc_id={doc_id}"
                    f"&variables={urllib.parse.quote(variables)}"
                )

                resp = await client.get(query_url, headers=headers)
                log.info("   GraphQL doc_id=%s → status=%d", doc_id, resp.status_code)
                failure = _observe_instagram_response(
                    resp, f"GraphQL legado doc_id={doc_id}"
                )

                # ── Detectar challenge/login redirect ──
                if failure is InstagramFailure.IP_RATE_LIMITED:
                    log.warning("   ⏳ GraphQL limitado pelo Instagram (429); cookies nao foram invalidados")
                    return None
                if failure in {InstagramFailure.COOKIE_INVALID, InstagramFailure.CHALLENGE}:
                    log.warning("   🍪 GraphQL rejeitou a conta: %s", failure.value)
                    return None

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except (json.JSONDecodeError, ValueError):
                        log.warning("   🍪 GraphQL retornou 200 mas body não é JSON")
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
    await _ig_wait_pacing()
    embed_url = f'https://www.instagram.com/{embed_path}/{shortcode}/embed/'

    cookies = cookies or {}
    try:
        embed_headers = {**BROWSER_HEADERS}
        if cookies:
            embed_headers['Cookie'] = _build_cookie_header(cookies)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=embed_headers)
            log.info("   Embed status: %d, body length: %d", resp.status_code, len(resp.text))
            failure = _observe_instagram_response(resp, "Embed")

            # ── Detectar challenge/login redirect ──
            if failure is InstagramFailure.IP_RATE_LIMITED:
                log.warning("   ⏳ Embed limitado pelo Instagram (429); cookies nao foram invalidados")
                return None
            if failure in {InstagramFailure.COOKIE_INVALID, InstagramFailure.CHALLENGE}:
                log.warning("   🍪 Embed rejeitou a conta: %s", failure.value)
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

async def _extract_via_ytdlp(url: str, cookie_path: str, out_dir: str) -> dict | None:
    """
    Usa yt-dlp com ou sem cookies para baixar vídeos/reels.
    Não funciona para fotos (retorna 'No video formats found').
    """
    log.info("🔌 Camada 4 (yt-dlp): %s", url)

    ydl_opts = {
        'outtmpl': os.path.join(out_dir, '%(id)s_%(index)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': False,
        'playlistend': int(_bounded_env_number('IG_MAX_STORY_ITEMS', 20, 1, 50)),
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 2,
        'merge_output_format': 'mp4',
        'format': 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'http_headers': {
            'User-Agent': BROWSER_HEADERS['User-Agent'],
        },
    }

    # Adiciona cookies se existirem. Usamos uma CÓPIA temporária para o
    # yt-dlp nunca sobrescrever o arquivo autenticado original (ele já chegou
    # a regravar instagram_cookies.txt removendo o sessionid e deixando só
    # cookies anônimos, o que derruba a Camada 1/API Interna).
    cookie_copy_path = None
    if cookie_path and os.path.exists(cookie_path):
        try:
            fd, cookie_copy_path = tempfile.mkstemp(prefix="ig_cookies_", suffix=".txt")
            with open(cookie_path, "rb") as fsrc:
                os.write(fd, fsrc.read())
            os.close(fd)
            ydl_opts['cookiefile'] = cookie_copy_path
        except Exception as e:
            log.warning("Falha ao copiar cookies para yt-dlp: %s", str(e)[:100])

    try:
        info = await baixar_com_ytdlp(
            url,
            ydl_opts,
            timeout=_bounded_env_number('IG_YTDLP_TIMEOUT', 180, 30, 1800),
        )

        arquivos = []

        def collect_files(item):
            if not isinstance(item, dict):
                return
            for child in item.get('entries') or []:
                collect_files(child)
            candidates = [item.get('filepath')]
            candidates.extend(
                download.get('filepath')
                for download in item.get('requested_downloads') or []
                if isinstance(download, dict)
            )
            for candidate in candidates:
                if candidate and os.path.isfile(candidate) and candidate not in arquivos:
                    arquivos.append(candidate)

        collect_files(info)

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
    finally:
        if cookie_copy_path and os.path.exists(cookie_copy_path):
            try:
                os.remove(cookie_copy_path)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Orquestrador Principal
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_instagram_user_id(username: str, cookies: dict) -> str | None:
    """Resolve username para o PK usado pelo endpoint de stories."""
    if not username or not cookies:
        return None
    await _ig_wait_pacing()
    headers = {**BROWSER_HEADERS, **IG_APP_HEADERS}
    headers['Cookie'] = _build_cookie_header(cookies)
    csrf = cookies.get('csrftoken', '')
    if csrf:
        headers['X-CSRFToken'] = csrf
    api_url = (
        'https://www.instagram.com/api/v1/users/web_profile_info/'
        f'?username={urllib.parse.quote(username)}'
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
        failure = _observe_instagram_response(resp, "Resolver usuário de story")
        if failure in {
            InstagramFailure.IP_RATE_LIMITED,
            InstagramFailure.COOKIE_INVALID,
            InstagramFailure.CHALLENGE,
        } or resp.status_code != 200:
            return None
        data = resp.json()
        user = ((data.get('data') or {}).get('user') or data.get('user') or {})
        actual_username = str(user.get('username') or '')
        if actual_username and actual_username.casefold() != username.casefold():
            log.warning(
                "Resposta de perfil não pertence ao story solicitado: %s != %s",
                actual_username,
                username,
            )
            return None
        user_id = user.get('id') or user.get('pk')
        return str(user_id) if user_id else None
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as exc:
        log.info("   Resolver usuário de story falhou: %s", str(exc)[:160])
        return None


def _story_reels_from_payload(data: dict) -> list[dict]:
    """Normaliza as duas formas conhecidas do payload de reels_media."""
    reels: list[dict] = []
    raw_reels = data.get('reels') or {}
    if isinstance(raw_reels, dict):
        reels.extend(value for value in raw_reels.values() if isinstance(value, dict))
    elif isinstance(raw_reels, list):
        reels.extend(value for value in raw_reels if isinstance(value, dict))
    raw_media = data.get('reels_media') or []
    if isinstance(raw_media, list):
        reels.extend(value for value in raw_media if isinstance(value, dict))
    return reels


def _parse_story_reels(data: dict, username: str = "") -> dict | None:
    """Converte uma sequência completa de stories, preservando a ordem da API."""
    reels = _story_reels_from_payload(data)
    if not reels:
        return None

    urls: list[str] = []
    media_types: list[str] = []
    uploader = username or 'Autor'
    caption = ''
    item_count = 0
    seen_item_ids: set[str] = set()
    for reel in reels:
        reel_user = reel.get('user') or reel.get('owner') or {}
        reel_username = str(reel_user.get('username') or '')
        if username and reel_username and reel_username.casefold() != username.casefold():
            continue
        if reel_username:
            uploader = reel_username
        items = reel.get('items') or []
        if not isinstance(items, list):
            return None
        if reel.get('more_available') or reel.get('has_more_available'):
            log.warning("Sequência de stories paginada/incompleta; rejeitando resultado")
            return None
        for item in items:
            if not isinstance(item, dict):
                return None
            item_id = str(item.get('pk') or item.get('id') or '')
            if item_id and item_id in seen_item_ids:
                continue
            parsed = _parse_api_item(item)
            if not parsed:
                log.warning("Story sem mídia utilizável; sequência parcial rejeitada")
                return None
            item_count += 1
            urls.extend(parsed['urls'])
            media_types.extend([parsed['type']] * len(parsed['urls']))
            if item_id:
                seen_item_ids.add(item_id)
            if not caption:
                caption = parsed.get('title') or ''

    if not urls or item_count == 0:
        return None
    if len(urls) != len(set(urls)):
        log.warning("Sequência de stories contém mídia duplicada; rejeitando")
        return None
    return {
        'urls': urls,
        'type': 'carousel' if len(urls) > 1 else (
            'video' if media_types and media_types[0] == 'video' else 'photo'
        ),
        'title': _sanitize_caption(caption),
        'uploader': uploader,
        '_expected_items': len(urls),
        '_complete': True,
        '_story_sequence': True,
    }


async def _extract_via_stories_api(username: str, cookies: dict) -> dict | None:
    """Busca diretamente todos os stories ativos de um perfil autenticado."""
    user_id = await _resolve_instagram_user_id(username, cookies)
    if not user_id:
        return None

    await _ig_wait_pacing()
    headers = {**BROWSER_HEADERS, **IG_APP_HEADERS}
    headers['Cookie'] = _build_cookie_header(cookies)
    csrf = cookies.get('csrftoken', '')
    if csrf:
        headers['X-CSRFToken'] = csrf
    api_url = (
        'https://i.instagram.com/api/v1/feed/reels_media/'
        f'?reel_ids={urllib.parse.quote(user_id)}&reel_flag=1'
    )
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
        failure = _observe_instagram_response(resp, "Sequência de stories")
        if failure in {
            InstagramFailure.IP_RATE_LIMITED,
            InstagramFailure.COOKIE_INVALID,
            InstagramFailure.CHALLENGE,
        } or resp.status_code != 200:
            return None
        return _parse_story_reels(resp.json(), username)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as exc:
        log.info("   Stories API falhou: %s", str(exc)[:200])
        return None


async def _extract_via_highlights_api(highlight_id: str, cookies: dict = None) -> dict | None:
    """Busca o conteúdo de um destaque do Instagram via API interna.

    Endpoint: i.instagram.com/api/v1/feed/reels_media/?reel_ids=highlight:<id>
    Retorna o mesmo formato padrão (urls/type/title/uploader) para reuso no caller.
    Requer cookies válidos (destaques são conteúdo do dono; público exige sessão).
    """
    log.info("🔗 Camada Highlights API: destaque %s", highlight_id)
    if not cookies:
        log.info("   ⏭️ Sem cookies — não dá pra abrir destaque (conteúdo do dono)")
        return None

    await _ig_wait_pacing()
    csrf = cookies.get('csrftoken', '')
    headers = {**BROWSER_HEADERS, **IG_APP_HEADERS}
    if csrf:
        headers['X-CSRFToken'] = csrf
    headers['Cookie'] = _build_cookie_header(cookies)

    api_url = (
        'https://i.instagram.com/api/v1/feed/reels_media/'
        f'?reel_ids=highlight%3A{highlight_id}&reel_flag=1'
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            log.info("   Highlights API status: %d", resp.status_code)
            failure = _observe_instagram_response(resp, "Highlights API")
            if failure in {
                InstagramFailure.IP_RATE_LIMITED,
                InstagramFailure.COOKIE_INVALID,
                InstagramFailure.CHALLENGE,
            }:
                return None
            if resp.status_code != 200:
                log.info("   Highlights API retornou %d", resp.status_code)
                return None
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                log.warning("   🍪 Highlights API body não é JSON")
                return None

        reels = data.get('reels', {})
        if not reels:
            log.info("   Highlights API vazia (destaque inexistente/privado?)")
            return None

        urls = []
        titulo = ""
        uploader = "Autor"
        for reel in reels.values():
            for item in reel.get('items', []):
                parsed = _parse_api_item(item)
                if not parsed:
                    continue
                urls.extend(parsed['urls'])
                if not titulo:
                    titulo = (item.get('caption') or {}).get('text', '') or parsed['title']
                if uploader == "Autor":
                    uploader = parsed['uploader']

        if not urls:
            return None

        reset_cookies_bad()  # sessão funcionou
        return {
            'urls': urls,
            'type': 'carousel' if len(urls) > 1 else (
                'video' if any(u.endswith(('.mp4', '.mov')) for u in urls) else 'photo'
            ),
            'title': _sanitize_caption(titulo),
            'uploader': uploader or 'Autor',
        }
    except Exception as e:
        log.info("   ❌ Highlights API falhou: %s", str(e)[:200])
        return None


async def download_instagram(
    url: str,
    cookie_path: str,
    out_dir: str,
    *,
    secondary_cookie_path: str = "",
) -> dict | None:
    """
    Download autenticado com duas contas, em ordem deterministica.

    Para cada conta configurada, tenta as rotas autenticadas adequadas ao tipo
    de URL e depois o yt-dlp com uma copia dos mesmos cookies. A conta
    secundaria so entra quando a primaria nao entrega uma midia valida.

    Retorna dict com:
      - urls: lista de URLs diretas da CDN, OU
      - files: lista de caminhos locais (quando yt-dlp baixa)
      - type: 'photo' | 'video' | 'carousel'
      - title: caption/legenda
      - uploader: nome do autor
      - _cookie_source: 'primary' ou 'secondary'
      - _primary_cookie_failed: True quando a secundaria salvou o download
    """
    log.info("📷 Instagram Extractor v2: %s", url)
    try:
        resolved_url = await resolve_instagram_share_url(url)
    except (ValueError, httpx.HTTPError) as exc:
        log.warning("❌ Link /share do Instagram rejeitado: %s", str(exc)[:160])
        return None
    if resolved_url != url:
        log.info("🔗 Instagram /share resolvido: %s", resolved_url)
        url = resolved_url
    shortcode = _get_shortcode(url)

    if not shortcode and not _is_highlight(url) and not _is_story(url):
        log.warning("❌ Não foi possível extrair shortcode de: %s", url)
        return None

    cookie_slots = (
        ("primary", cookie_path),
        ("secondary", secondary_cookie_path),
    )
    primary_failed = False
    for slot, path in cookie_slots:
        if not path or not os.path.exists(path):
            log.warning("🍪 Cookies Instagram %s nao configurados: %s", slot, path or "(vazio)")
            if slot == "primary":
                primary_failed = True
            continue
        account_id = _instagram_account_pool.register(slot, path)
        cookies = _load_cookies_from_file(path)
        if not cookies or 'sessionid' not in cookies:
            log.warning("🍪 Cookies Instagram %s vazios ou invalidos", slot)
            _instagram_account_pool.report_failure(
                account_id,
                'cookie_file',
                InstagramFailure.COOKIE_INVALID,
                "Arquivo de cookies vazio ou sem sessionid",
            )
            if slot == "primary":
                primary_failed = True
            continue

        log.info("🍪 Tentando conta Instagram %s", slot)
        if _is_highlight(url):
            result = await _run_account_endpoint(
                account_id,
                'highlights_api',
                _extract_via_highlights_api,
                _get_highlight_id(url),
                cookies,
            )
        elif _is_story(url):
            story_info = _get_story_info(url)
            story_media_id = story_info[1] if story_info else None
            if story_media_id:
                result = await _run_account_endpoint(
                    account_id,
                    'story_media_api',
                    _extract_via_api_media_id,
                    story_media_id,
                    cookies,
                )
            else:
                result = await _run_account_endpoint(
                    account_id,
                    'stories_api',
                    _extract_via_stories_api,
                    story_info[0],
                    cookies,
                ) if story_info else None
        else:
            result = await _try_all_layers(
                shortcode, cookies, url, account_id=account_id
            )

        # Segunda forma de leitura da MESMA conta: o yt-dlp recebe somente o
        # arquivo de cookies deste slot. Isso contorna bloqueios do endpoint de
        # metadados sem recorrer a sessao anonima ou a outra fonte.
        if not _is_acceptable_result_for_url(result, url):
            log.info("🍪 Rotas da conta %s falharam; tentando extrator autenticado", slot)
            result = await _run_account_endpoint(
                account_id, 'ytdlp', _extract_via_ytdlp, url, path, out_dir
            )

        if result and shortcode and not result.get('title'):
            post_meta = await _fetch_post_meta_via_oembed(shortcode, _get_embed_path(url))
            if post_meta and post_meta.get('title'):
                result['title'] = post_meta['title']
                if post_meta.get('uploader') and (
                    not result.get('uploader') or result.get('uploader') == 'Autor'
                ):
                    result['uploader'] = post_meta['uploader']

        if _is_acceptable_result_for_url(result, url):
            result['_cookie_source'] = slot
            result['_primary_cookie_failed'] = primary_failed
            reset_cookies_bad(reset_pool=False)
            log.info("✅ Instagram via conta %s: %s", slot, url)
            return result
        if slot == "primary":
            primary_failed = True
            log.warning("⚠️ Conta Instagram primaria falhou; tentando secundaria")

    log.warning("❌ As duas contas de cookies falharam para: %s", url)
    return None


def _is_acceptable_result_for_url(result: dict | None, url: str) -> bool:
    """Evita tratar thumbnail de Reel como extração final."""
    if not result:
        return False
    if _is_reel(url) and result.get('type') == 'photo':
        log.info("⏭️ Resultado de Reel veio como foto; ignorando thumbnail e tentando fallback de vídeo")
        return False
    return True


async def _try_all_layers(
    shortcode: str,
    cookies: dict,
    url: str,
    *,
    account_id: str = "",
) -> dict | None:
    """Tenta as 3 camadas de extração (API, GraphQL, Embed) com os cookies fornecidos."""

    # Se o IP ainda esta em cooldown de 429, martelar API/GraphQL/Embed de novo
    # so devolve 302->login/429 e reforca o sinal de automacao. Vai direto pro
    # yt-dlp (CDN), que continua funcionando mesmo nessa condicao.
    if _ig_429_recente():
        log.info("⏭️ Cooldown de 429 ativo — pulando camadas 1-3, direto para yt-dlp")
        return None

    # Cada camada ja aplica o pacing global antes de bater no Instagram.

    # ── Camada 1: API Interna ──
    result = await _run_account_endpoint(
        account_id, 'media_api', _extract_via_api, shortcode, cookies
    )
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via API Interna: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ API Interna falhou, tentando Camada 2...")

    # ── Camada 2: GraphQL ──
    result = await _run_account_endpoint(
        account_id, 'graphql', _extract_via_graphql, shortcode, cookies
    )
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via GraphQL: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ GraphQL falhou, tentando Camada 3...")

    # ── Camada 3: Embed Scraping ──
    result = await _run_account_endpoint(
        account_id,
        'embed',
        _extract_via_embed,
        shortcode,
        cookies,
        _get_embed_path(url),
    )
    if _is_acceptable_result_for_url(result, url):
        log.info("✅ Instagram download via Embed: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ Embed falhou...")

    return None
