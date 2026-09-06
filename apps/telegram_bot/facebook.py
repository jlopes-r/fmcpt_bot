"""Read public post data without treating previews or avatars as post photos."""
import json
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import aiohttp

from packages.url_utils import is_facebook_url

ACCESS_NOTICE = ('🔒 Esta publicação está restrita ou exige login no Facebook. '
                 'O bot não tem acesso a esse conteúdo. Envie um link de uma publicação pública.')
UNAVAILABLE_NOTICE = ('⚠️ Não consegui acessar esta publicação do Facebook. '
                      'Ela pode estar indisponível ou usar um formato ainda não suportado. '
                      'Não foi possível confirmar se é privada.')


class FacebookAccessRestricted(ValueError):
    """Facebook explicitly requested authentication."""


def requires_login(html):
    soup = BeautifulSoup(html, 'html.parser')
    return any(form.find('input', attrs={'type': 'password'}) is not None
               and '/login' in form.get('action', '')
               for form in soup.find_all('form'))


def parse_public_post(html):
    soup = BeautifulSoup(html, 'html.parser')
    stories = []
    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if node.get('__typename') == 'Story' and (node.get('attachments') or node.get('message')):
                stories.append(node)
                return
            for item in node.values():
                visit(item)
    for script in soup.find_all('script', type='application/json'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, RecursionError):
            continue
    # Multiple stories may be recommendations. Never merge unrelated posts.
    if len(stories) != 1:
        return None
    story = stories[0]
    photos = []
    has_video = False
    def attachments(node):
        nonlocal has_video
        if isinstance(node, list):
            for item in node:
                attachments(item)
        elif isinstance(node, dict):
            kind = node.get('__typename')
            if kind == 'Video':
                has_video = True
                return
            if kind == 'Photo':
                for field in ('image', 'photo_image', 'large_share_image'):
                    image = node.get(field) or {}
                    uri = image.get('uri') if isinstance(image, dict) else None
                    host = urlparse(uri or '').hostname or ''
                    if uri and uri.startswith('https://') and (host.endswith('.fbcdn.net') or host.endswith('.fbsbx.com')):
                        if uri not in photos:
                            photos.append(uri)
                        break
                return
            for value in node.values():
                attachments(value)
    attachments(story.get('attachments'))
    message = story.get('message') or {}
    text = message.get('text', '') if isinstance(message, dict) else ''
    actors = story.get('actors') or []
    return {'text': text, 'photos': photos, 'has_video': has_video,
            'author': actors[0].get('name', 'Facebook') if actors else 'Facebook'}


async def fetch_public_post(session, url):
    for _ in range(5):
        if not is_facebook_url(url):
            raise ValueError('Redirecionamento fora do Facebook')
        if urlparse(url).path.rstrip('/') in ('/login', '/login.php', '/checkpoint'):
            raise FacebookAccessRestricted()
        async with session.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=25)) as response:
            if response.status in (301, 302, 303, 307, 308):
                from urllib.parse import urljoin
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError('Página do Facebook excede limite de leitura')
            html = body.decode('utf-8', errors='replace')
            post = parse_public_post(html)
            if post is None and requires_login(html):
                raise FacebookAccessRestricted()
            return post
    raise ValueError('Muitos redirecionamentos do Facebook')
