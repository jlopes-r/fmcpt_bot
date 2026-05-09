import re
import os
import json
import asyncio
import logging
from functools import partial
import yt_dlp
import aiohttp
import instaloader
import httpx

log = logging.getLogger("SuperBot")

# Instancia global do instaloader para evitar recriar sempre
L = instaloader.Instaloader(
    download_pictures=True,
    download_video_thumbnails=False,
    download_videos=True,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

# Regex para extrair URLs de imagens e videos do embed
IMG_REGEX = re.compile(r'class="EmbeddedMediaImage"[^>]*src="([^"]+)"')
VIDEO_REGEX = re.compile(r'class="EmbeddedVideoPlayer"[^>]*src="([^"]+)"')
SHORTCODE_REGEX = re.compile(r'/(?:p|reel|ad|tv)/([A-Za-z0-9_-]+)')


def _get_shortcode(url: str) -> str | None:
    """Extrai o shortcode do Instagram da URL."""
    match = SHORTCODE_REGEX.search(url)
    if match:
        return match.group(1)
    return None


def _run_ytdlp(url: str, ydl_opts: dict) -> dict:
    """Executa yt-dlp em thread separada."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


async def download_with_cookies(
    url: str,
    cookie_path: str,
    out_dir: str,
    timeout: int = 60
) -> dict | None:
    """
    Tenta download via yt-dlp com cookies de autenticação.
    
    Retorna dict com:
      - type: 'photo', 'video', 'carousel'
      - files: lista de paths dos arquivos baixados
      - title: titulo/descrição
      - uploader: nome do autor
    """
    if not os.path.exists(cookie_path):
        log.warning("Arquivo de cookies não encontrado: %s", cookie_path)
        return None

    loop = asyncio.get_running_loop()
    
    ydl_opts = {
        'outtmpl': os.path.join(out_dir, '%(id)s_%(index)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': False,
        'extract_flat': False,
        'cookiefile': cookie_path,
        'socket_timeout': timeout,
        'retries': 3,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        },
    }

    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, partial(_run_ytdlp, url, ydl_opts)),
            timeout=timeout
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

        return {
            'type': 'carousel' if len(arquivos) > 1 else ('video' if arquivos[0].endswith(('.mp4', '.mov')) else 'photo'),
            'files': arquivos,
            'title': info.get('title') or info.get('description') or '',
            'uploader': info.get('uploader') or info.get('channel') or 'Autor',
        }

    except asyncio.TimeoutError:
        log.warning("yt-dlp timeout para Instagram: %s", url)
        return None
    except Exception as e:
        log.debug("yt-dlp com cookies falhou: %s", str(e)[:150])
        return None


async def download_via_embed(url: str) -> dict | None:
    """
    Fallback: extrai mídia via Instagram embed endpoint.
    Funciona para posts públicos sem necessidade de login.
    """
    shortcode = _get_shortcode(url)
    if not shortcode:
        return None

    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(embed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

        # Tenta o novo formato HTML (img tag direta)
        img_urls = IMG_REGEX.findall(html)
        video_urls = VIDEO_REGEX.findall(html)
        
        # Se falhou, tenta o formato antigo (json-like no html)
        if not img_urls:
            img_urls = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
        if not video_urls:
            video_urls = re.findall(r'"video_url"\s*:\s*"([^"]+)"', html)
        
        # Limpa os links (remove escape characters e amp;)
        img_urls = [u.replace('\\/', '/').replace('&amp;', '&') for u in img_urls]
        video_urls = [u.replace('\\/', '/').replace('&amp;', '&') for u in video_urls]
        
        # Pega a melhor resolução se houver várias (no novo formato costuma ser a última do srcset)
        srcset_match = re.search(r'srcset="([^"]+)"', html)
        if srcset_match and img_urls:
            last_img = srcset_match.group(1).split(',')[-1].split(' ')[0]
            if last_img.startswith('http'):
                img_urls[0] = last_img.replace('&amp;', '&')
        
        # Extrai caption
        caption = ''
        caption_match = re.search(r'"caption"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
        if caption_match:
            caption = caption_match.group(1).encode().decode('unicode_escape')
        
        # Extrai autor
        author = ''
        author_match = re.search(r'"username"\s*:\s*"([^"]+)"', html)
        if author_match:
            author = author_match.group(1)

        midias = []
        if video_urls:
            for v_url in video_urls:
                midias.append({'type': 'video', 'url': v_url.replace('\\/', '/')})
        elif img_urls:
            for i_url in img_urls:
                midias.append({'type': 'photo', 'url': i_url.replace('\\/', '/')})

        if not midias:
            return None

        return {
            'type': 'carousel' if len(midias) > 1 else midias[0]['type'],
            'urls': [m['url'] for m in midias],
            'title': caption,
            'uploader': author,
        }

    except asyncio.TimeoutError:
        log.warning("Embed timeout para Instagram: %s", url)
        return None
    except Exception as e:
        log.debug("Embed fallback falhou: %s", str(e)[:150])
        return None


async def download_via_instaloader(url: str, out_dir: str) -> dict | None:
    """Fallback 2: Tenta usar o Instaloader para baixar fotos e álbuns."""
    shortcode = _get_shortcode(url)
    if not shortcode:
        return None

    log.info("Tentando Instaloader para shortcode: %s", shortcode)
    try:
        # Importante: rodar o instaloader em background para nǜo bloquear
        loop = asyncio.get_running_loop()
        
        def _get_post_info():
            # Create a new context specifically for this attempt to avoid stale state
            local_L = instaloader.Instaloader(
                download_pictures=False,
                download_video_thumbnails=False,
                download_videos=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                max_connection_attempts=1
            )
            
            try:
                # Load session from cookie file se existir
                cookie_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "instagram_cookies.txt")
                if os.path.exists(cookie_file):
                    import http.cookiejar
                    cj = http.cookiejar.MozillaCookieJar(cookie_file)
                    cj.load(ignore_discard=True, ignore_expires=True)
                    for cookie in cj:
                        local_L.context._session.cookies.set_cookie(cookie)
                    log.info("Cookies carregados no Instaloader com sucesso!")
            except Exception as e:
                log.warning(f"Falha ao carregar cookies no Instaloader: {e}")

            post = instaloader.Post.from_shortcode(local_L.context, shortcode)
            
            media_urls = []
            if post.typename == 'GraphSidecar':
                for node in post.get_sidecar_nodes():
                    if node.is_video:
                        media_urls.append({'type': 'video', 'url': node.video_url})
                    else:
                        media_urls.append({'type': 'photo', 'url': node.display_url})
            else:
                if post.is_video:
                    media_urls.append({'type': 'video', 'url': post.video_url})
                else:
                    media_urls.append({'type': 'photo', 'url': post.url})
                    
            return {
                'urls': [m['url'] for m in media_urls],
                'type': 'carousel' if len(media_urls) > 1 else media_urls[0]['type'],
                'title': '', # Title was causing encoding errors
                'uploader': post.owner_username if post.owner_username else 'Autor'
            }
            
        # Adicionado timeout estrito para evitar que o instaloader trave a thread para sempre
        result = await asyncio.wait_for(loop.run_in_executor(None, _get_post_info), timeout=15.0)
        return result
        
    except asyncio.TimeoutError:
        log.warning("Instaloader demorou muito (timeout) para shortcode: %s", shortcode)
        return None
    except Exception as e:
        log.warning("Instaloader falhou: %s", str(e)[:150])
        return None

async def download_via_rapidapi(url: str) -> dict | None:
    """Fallback 3: Usa uma API pública ou proxy para baixar (Cobalt API v2 - POST /)."""
    shortcode = _get_shortcode(url)
    if not shortcode:
        return None
        
    log.info("Tentando API externa (Cobalt Network) para shortcode: %s", shortcode)
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            # 1. Pega lista de instâncias públicas do Cobalt que estão ativas
            instances = []
            try:
                resp_instances = await client.get("https://instances.cobalt.best/instances.json", headers=headers)
                if resp_instances.status_code == 200:
                    data = resp_instances.json()
                    for inst in data:
                        # Suporta formato antigo (online/services) e novo (status)
                        is_online = inst.get("online") or inst.get("status") == "online"
                        supports_ig = True  # Assume suporte se não especificado
                        if "services" in inst:
                            supports_ig = inst["services"].get("instagram", False)
                        if is_online and supports_ig:
                            # Suporta tanto 'api' quanto 'url' como chave
                            api_host = inst.get("api") or inst.get("url", "").replace("https://", "").replace("http://", "")
                            protocol = inst.get("protocol", "https")
                            if api_host:
                                api_url = f"{protocol}://{api_host}" if "://" not in api_host else api_host
                                instances.append(api_url.rstrip("/"))
            except Exception as e:
                log.debug("Não foi possível buscar lista de instâncias cobalt: %s", e)
                
            # Adiciona instâncias fixas de backup caso a busca falhe
            if not instances:
                instances = [
                    "https://cobalt-api.libly.org",
                    "https://cobalt.api.g-p.io",
                    "https://cobalt.vinid.de",
                    "https://api.cobalt.tools", # Auth required, but keeping as last resort
                ]
                
            post_url = f"https://www.instagram.com/p/{shortcode}/"
            
            for api_base in instances:
                try:
                    log.debug("Tentando Cobalt API: %s", api_base)
                    # API v2: POST / (raiz) — /api/json foi deprecado em Nov 2024
                    resp = await client.post(
                        f"{api_base}/", 
                        json={"url": post_url}, 
                        headers=headers
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        status = data.get("status", "")
                        
                        if status == "tunnel" or status == "redirect":
                            if "url" in data:
                                return {
                                    'type': 'video',
                                    'urls': [data["url"]],
                                    'title': '',
                                    'uploader': 'Autor'
                                }
                        elif status == "picker":
                            urls = [item["url"] for item in data.get("picker", []) if "url" in item]
                            if urls:
                                return {
                                    'type': 'carousel',
                                    'urls': urls,
                                    'title': '',
                                    'uploader': 'Autor'
                                }
                    elif resp.status_code == 401:
                        log.debug("Cobalt %s requer autenticação, pulando...", api_base)
                        continue
                except Exception as e:
                    log.debug("API %s falhou: %s", api_base, str(e)[:100])
                    continue
                    
    except Exception as e:
        log.warning("Cobalt Network falhou completamente: %s", str(e)[:150])
        
    return None

async def download_via_embed_v2(url: str) -> dict | None:
    """Fallback 4: Puxa o HTML do embed e raspa a tag de imagem diretamente."""
    shortcode = _get_shortcode(url)
    if not shortcode:
        return None

    log.info("Tentando Embed V2 para shortcode: %s", shortcode)
    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(embed_url, headers=headers)
            if resp.status_code != 200:
                return None
            html = resp.text
            
        # Puxa o src direto usando regex simples
        img_match = re.findall(r'class=.EmbeddedMediaImage.[^>]*src=.([^>]+).', html)
        if img_match:
            # A regex pode pegar o srcset junto. Vamos pegar só o primeiro link (src original)
            img_url = img_match[0].split('"')[0].replace('&amp;', '&').replace('\\/', '/')
            
            return {
                'type': 'photo',
                'urls': [img_url],
                'title': '',
                'uploader': 'Autor'
            }
            
    except Exception as e:
        log.warning("Embed V2 falhou: %s", str(e)[:150])
    return None

async def download_instagram(
    url: str,
    cookie_path: str,
    out_dir: str
) -> dict | None:
    """
    Fluxo completo de download do Instagram:
    1. Tenta Instaloader primeiro (excelente para fotos/álbuns)
    2. Tenta API Externa/Cobalt
    3. Tenta yt-dlp com cookies
    4. Fallback para embed endpoint
    
    Retorna dict com info da mdia ou None se tudo falhar.
    """
    log.info("Y\" Tentando download Instagram: %s", url)

    # Tentativa 1: Instaloader (Melhor para fotos/carrossel, yt-dlp costuma falhar nelas)
    result = await download_via_instaloader(url, out_dir)
    if result:
        log.info("o. Instagram download via Instaloader: %s (%d itens)", url, len(result.get('urls', [])))
        return result
    log.info("s? Instaloader falhou, tentando próxima...")

    # Tentativa 2: API Externa (Cobalt)
    result = await download_via_rapidapi(url)
    if result:
        log.info("o. Instagram download via Cobalt API: %s (%d itens)", url, len(result.get('urls', [])))
        return result
    log.info("s? APIs externas falharam, tentando próxima...")

    # Tentativa 3: yt-dlp com cookies (Bom para vídeos fechados/reels pesados)
    if os.path.exists(cookie_path):
        result = await download_with_cookies(url, cookie_path, out_dir)
        if result:
            log.info("o. Instagram download via cookies: %s (%d arquivos)", url, len(result.get('files', [])))
            return result
        log.info("s? Cookies/yt-dlp falharam, tentando embed fallback...")

    # Tentativa 4: Embed endpoint
    result = await download_via_embed(url)
    if result:
        log.info("o. Instagram download via embed: %s (%d itens)", url, len(result.get('files', result.get('urls', []))))
        return result
        
    # Tentativa 5: Embed endpoint Direto/HTML Scraping
    result = await download_via_embed_v2(url)
    if result:
        log.info("o. Instagram download via embed v2: %s (%d itens)", url, len(result.get('urls', [])))
        return result

    log.warning("?O Todas as tentativas falharam para: %s", url)
    return None
