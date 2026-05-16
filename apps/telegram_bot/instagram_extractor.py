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
import asyncio
import logging
import urllib.parse
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

    uploader = item.get('user', {}).get('username', 'Autor')

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

    uploader = media.get('owner', {}).get('username', 'Autor')

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

async def _extract_via_api(shortcode: str) -> dict | None:
    """
    Usa a API interna do Instagram: i.instagram.com/api/v1/media/{media_id}/info/
    Essa é a mesma API que o app móvel usa. Não precisa de login.
    """
    media_id = _shortcode_to_media_id(shortcode)
    log.info("🔌 Camada 1 (API Interna): shortcode=%s → media_id=%s", shortcode, media_id)

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Passo 1: "aquecer" a sessão visitando a página do post (pega cookies)
            post_url = f'https://www.instagram.com/p/{shortcode}/'
            warmup = await client.get(post_url, headers=BROWSER_HEADERS)
            log.info("   Warmup status: %d", warmup.status_code)

            # Pega csrftoken do cookie
            csrf = ''
            for cookie_name, cookie_value in client.cookies.items():
                if cookie_name == 'csrftoken':
                    csrf = cookie_value

            # Passo 2: Bater na API interna
            api_url = f'https://i.instagram.com/api/v1/media/{media_id}/info/'
            headers = {
                **BROWSER_HEADERS,
                **IG_APP_HEADERS,
            }
            if csrf:
                headers['X-CSRFToken'] = csrf

            resp = await client.get(api_url, headers=headers)
            log.info("   API resp status: %d", resp.status_code)

            if resp.status_code == 200:
                data = resp.json()
                items = data.get('items', [])
                if items:
                    result = _parse_api_item(items[0])
                    if result:
                        log.info("   ✅ API retornou %d URLs", len(result['urls']))
                        return result
                log.info("   API retornou JSON mas sem itens válidos")
            else:
                log.info("   API retornou %d: %s", resp.status_code, resp.text[:100])

    except Exception as e:
        log.info("   ❌ API Interna falhou: %s", str(e)[:150])

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Camada 2 — GraphQL com doc_id público
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_via_graphql(shortcode: str) -> dict | None:
    """
    Usa o endpoint GraphQL público com o doc_id mais recente.
    Pode quebrar se o Instagram rotacionar o doc_id, mas é fácil de atualizar.
    """
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
            # Aquecer sessão
            warmup = await client.get(f'https://www.instagram.com/p/{shortcode}/', headers=BROWSER_HEADERS)
            csrf = ''
            for cookie_name, cookie_value in client.cookies.items():
                if cookie_name == 'csrftoken':
                    csrf = cookie_value

            headers = {
                **BROWSER_HEADERS,
                **IG_APP_HEADERS,
                'X-CSRFToken': csrf,
                'X-Requested-With': 'XMLHttpRequest',
            }

            for doc_id in doc_ids:
                query_url = (
                    f"https://www.instagram.com/graphql/query/"
                    f"?doc_id={doc_id}"
                    f"&variables={urllib.parse.quote(variables)}"
                )

                resp = await client.get(query_url, headers=headers)
                log.info("   GraphQL doc_id=%s → status=%d", doc_id, resp.status_code)

                if resp.status_code == 200:
                    data = resp.json()
                    # Formato novo (xdt_shortcode_media)
                    media = data.get('data', {}).get('xdt_shortcode_media')
                    # Formato antigo
                    if not media:
                        media = data.get('data', {}).get('shortcode_media')

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

async def _extract_via_embed(shortcode: str) -> dict | None:
    """
    Faz scraping da página de embed do Instagram.
    Procura por __additionalDataLoaded, _sharedData, ou tags meta OG.
    """
    log.info("🔌 Camada 3 (Embed Scraping): shortcode=%s", shortcode)
    embed_url = f'https://www.instagram.com/p/{shortcode}/embed/'

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=BROWSER_HEADERS)
            log.info("   Embed status: %d, body length: %d", resp.status_code, len(resp.text))

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
        log.info("   ❌ yt-dlp falhou: %s", str(e)[:150])
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
    """
    log.info("📷 Instagram Extractor v2: %s", url)
    shortcode = _get_shortcode(url)

    # Stories: vai direto pro yt-dlp (único método que funciona)
    if _is_story(url):
        log.info("📖 URL de Story detectada, usando yt-dlp direto...")
        result = await _extract_via_ytdlp(url, cookie_path, out_dir)
        if result:
            return result
        log.warning("❌ Todas as tentativas falharam para Story: %s", url)
        return None

    if not shortcode:
        log.warning("❌ Não foi possível extrair shortcode de: %s", url)
        return None

    # ── Camada 1: API Interna ──
    result = await _extract_via_api(shortcode)
    if result:
        log.info("✅ Instagram download via API Interna: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ API Interna falhou, tentando Camada 2...")

    # ── Camada 2: GraphQL ──
    result = await _extract_via_graphql(shortcode)
    if result:
        log.info("✅ Instagram download via GraphQL: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ GraphQL falhou, tentando Camada 3...")

    # ── Camada 3: Embed Scraping ──
    result = await _extract_via_embed(shortcode)
    if result:
        log.info("✅ Instagram download via Embed: %s (%d itens)", url, len(result['urls']))
        return result
    log.info("⏭️ Embed falhou, tentando Camada 4...")

    # ── Camada 4: yt-dlp ──
    result = await _extract_via_ytdlp(url, cookie_path, out_dir)
    if result:
        log.info("✅ Instagram download via yt-dlp: %s", url)
        return result

    log.warning("❌ Todas as 4 camadas falharam para: %s", url)
    return None
