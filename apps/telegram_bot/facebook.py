"""Read public Facebook content without mixing recommended posts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse
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


@dataclass(frozen=True)
class FacebookTarget:
    """Content kind and identity encoded by a Facebook URL."""

    kind: str
    content_id: str = ""


_CONTENT_ID_PATTERN = r"[A-Za-z0-9._-]+"
_CONTENT_NODE_TYPES = {"story", "post", "reel", "video"}
_MEDIA_NODE_TYPES = {"photo", "video"}
_ID_FIELDS = {"id", "legacy_fbid", "post_id", "story_fbid", "story_id", "video_id"}
_URL_FIELDS = {"url", "wwwurl", "permalink", "permalink_url", "canonical_url", "webpage_url"}
_IGNORED_BRANCHES = {"actor", "actors", "author", "owner", "profile", "profiles", "comments"}


def _query_value(query: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = query.get(name) or []
        if values and values[0].strip():
            return values[0].strip()
    return ""


def parse_facebook_target(url: str) -> FacebookTarget | None:
    """Derive post/reel/story/video and the expected content ID from a URL."""
    if not is_facebook_url(url):
        return None
    parsed = urlparse(url)
    path = unquote(parsed.path or "/")
    query = parse_qs(parsed.query, keep_blank_values=False)
    patterns = (
        ("story", rf"/stories/[^/]+/({_CONTENT_ID_PATTERN})(?:/|$)"),
        ("reel", rf"/reels?/({_CONTENT_ID_PATTERN})(?:/|$)"),
        ("video", rf"/videos/(?:[^/]+/)?({_CONTENT_ID_PATTERN})(?:/|$)"),
        ("post", rf"/posts/({_CONTENT_ID_PATTERN})(?:/|$)"),
        ("post", rf"/photos/(?:[^/]+/)?({_CONTENT_ID_PATTERN})(?:/|$)"),
    )
    for kind, pattern in patterns:
        match = re.search(pattern, path, flags=re.IGNORECASE)
        if match:
            return FacebookTarget(kind, match.group(1))
    lowered = path.rstrip("/").lower()
    if lowered in {"/watch", "/video.php"}:
        return FacebookTarget("video", _query_value(query, "v", "video_id"))
    if lowered == "/photo.php":
        return FacebookTarget("post", _query_value(query, "fbid"))
    if lowered in {"/permalink.php", "/story.php"}:
        return FacebookTarget("post", _query_value(query, "story_fbid", "fbid"))
    share = re.search(r"/share/([prsv])(?:/|$)", path, flags=re.IGNORECASE)
    if share:
        kind = {"r": "reel", "s": "story", "v": "video", "p": "post"}[share.group(1).lower()]
        return FacebookTarget(kind)
    if (parsed.hostname or "").lower().endswith("fb.watch"):
        return FacebookTarget("video")
    return FacebookTarget("unknown")


def extract_facebook_content_id(url: str) -> str:
    target = parse_facebook_target(url)
    return target.content_id if target else ""


def requires_login(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    if any(
        form.find("input", attrs={"type": "password"}) is not None
        and "/login" in form.get("action", "")
        for form in soup.find_all("form")
    ):
        return True
    lowered = html.lower()
    return any(
        marker in lowered
        for marker in (
            'id="login_form"',
            "you must log in to continue",
            "content isn't available right now",
        )
    )


def extract_canonical_facebook_url(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    if canonical:
        candidates.append(canonical.get("href"))
    og_url = soup.find("meta", attrs={"property": "og:url"})
    if og_url:
        candidates.append(og_url.get("content"))
    for candidate in candidates:
        if isinstance(candidate, str) and is_facebook_url(candidate):
            return candidate
    return ""


def _json_story_candidates(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    stories: list[dict[str, Any]] = []
    fingerprints: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        node_type = str(node.get("__typename") or node.get("type") or "").lower()
        looks_like_content = node_type in _CONTENT_NODE_TYPES and any(
            key in node
            for key in ("attachments", "message", "playable_url", "playable_url_quality_hd")
        )
        if looks_like_content:
            try:
                fingerprint = json.dumps(node, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                fingerprint = repr(node)
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                stories.append(node)
            # Do not promote recommendations nested inside a selected content node.
            return
        for value in node.values():
            visit(value)

    for script in soup.find_all("script", type="application/json"):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (TypeError, ValueError, RecursionError):
            continue
    return stories


def _id_matches(value: Any, expected_id: str) -> bool:
    text = str(value or "")
    if not text or not expected_id:
        return False
    if text == expected_id:
        return True
    return expected_id in re.split(r"[^A-Za-z0-9._-]+", text)


def _candidate_identity_score(candidate: dict[str, Any], expected_id: str) -> int:
    if not expected_id:
        return 0
    score = 0

    def visit(node: Any, *, depth: int = 0, branch: str = "") -> None:
        nonlocal score
        if isinstance(node, list):
            for item in node:
                visit(item, depth=depth + 1, branch=branch)
            return
        if not isinstance(node, dict):
            return
        node_type = str(node.get("__typename") or node.get("type") or "").lower()
        identity_node = depth == 0 or node_type in (_CONTENT_NODE_TYPES | _MEDIA_NODE_TYPES)
        identity_branch = branch in {"attachments", "attachment", "media", "target", "story"}
        for key, value in node.items():
            lowered_key = str(key).lower()
            if lowered_key in _IGNORED_BRANCHES:
                continue
            if isinstance(value, (str, int)):
                if lowered_key in _URL_FIELDS:
                    target = parse_facebook_target(str(value))
                    if target and target.content_id == expected_id:
                        score = max(score, 100)
                    elif expected_id in str(value):
                        score = max(score, 80)
                elif lowered_key in _ID_FIELDS and (identity_node or identity_branch):
                    if _id_matches(value, expected_id):
                        score = max(score, 100 if depth == 0 else 90)
            else:
                visit(value, depth=depth + 1, branch=lowered_key)

    visit(candidate)
    return score


def _contains_video(node: Any) -> bool:
    if isinstance(node, list):
        return any(_contains_video(item) for item in node)
    if not isinstance(node, dict):
        return False
    node_type = str(node.get("__typename") or node.get("type") or "").lower()
    return node_type in {"video", "reel"} or any(_contains_video(value) for value in node.values())


def _select_story(
    stories: list[dict[str, Any]], expected_id: str, content_type: str
) -> dict[str, Any] | None:
    if expected_id:
        matches = [
            (_candidate_identity_score(story, expected_id), index, story)
            for index, story in enumerate(stories)
        ]
        matches = [entry for entry in matches if entry[0] > 0]
        if not matches:
            return None
        matches.sort(key=lambda entry: (entry[0], _contains_video(entry[2])), reverse=True)
        return matches[0][2]
    candidates = stories
    if content_type in {"video", "reel"}:
        video_candidates = [story for story in stories if _contains_video(story)]
        if len(video_candidates) == 1:
            candidates = video_candidates
    # Without identity, choosing between several Stories could send a recommendation.
    return candidates[0] if len(candidates) == 1 else None


def _safe_media_url(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        return ""
    host = (urlparse(value).hostname or "").lower()
    return value if host.endswith(".fbcdn.net") or host.endswith(".fbsbx.com") else ""


def _find_uri(value: Any) -> str:
    if isinstance(value, str):
        return _safe_media_url(value)
    if isinstance(value, list):
        for item in value:
            found = _find_uri(item)
            if found:
                return found
    elif isinstance(value, dict):
        for key in ("uri", "url", "src"):
            found = _find_uri(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _find_uri(child)
            if found:
                return found
    return ""


def _media_from_story(story: dict[str, Any]) -> list[dict[str, str]]:
    media: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, url: str, source_id: str) -> None:
        key = (kind, url, source_id)
        if key not in seen:
            seen.add(key)
            media.append({"type": kind, "url": url, "id": source_id})

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        kind = str(node.get("__typename") or node.get("type") or "").lower()
        source_id = str(node.get("id") or node.get("legacy_fbid") or node.get("video_id") or "")
        if kind == "video":
            url = ""
            for field in (
                "playable_url_quality_hd",
                "browser_native_hd_url",
                "playable_url",
                "browser_native_sd_url",
                "dash_manifest_url",
            ):
                url = _find_uri(node.get(field))
                if url:
                    break
            add("video", url, source_id)
            return  # Video thumbnails are not post photos.
        if kind == "photo":
            url = ""
            for field in ("image", "photo_image", "large_share_image", "viewer_image"):
                url = _find_uri(node.get(field))
                if url:
                    break
            if url:
                add("photo", url, source_id)
            return
        for key, value in node.items():
            if str(key).lower() not in _IGNORED_BRANCHES:
                visit(value)

    visit(story)
    return media


def _story_text(story: dict[str, Any]) -> str:
    message = story.get("message")
    if isinstance(message, dict) and isinstance(message.get("text"), str):
        return message["text"]
    if isinstance(message, str):
        return message
    for key in ("title", "text", "description"):
        value = story.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            return value["text"]
    return ""


def _story_author(story: dict[str, Any]) -> str:
    actors = story.get("actors") or []
    if isinstance(actors, list):
        for actor in actors:
            if isinstance(actor, dict) and actor.get("name"):
                return str(actor["name"])
    for key in ("author", "owner"):
        owner = story.get(key)
        if isinstance(owner, dict) and owner.get("name"):
            return str(owner["name"])
    return "Facebook"


def _story_source_id(story: dict[str, Any], expected_id: str) -> str:
    if expected_id:
        return expected_id
    for key in ("post_id", "story_fbid", "legacy_fbid", "video_id", "id"):
        value = story.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def parse_public_post(
    html: str, expected_id: str = "", content_type: str = ""
) -> dict[str, Any] | None:
    """Extract only the Story matching ``expected_id``.

    If the URL has no identity, retain the conservative behavior of accepting
    only one unambiguous candidate.
    """
    story = _select_story(
        _json_story_candidates(html), str(expected_id or ""), content_type.lower()
    )
    if story is None:
        return None
    media = _media_from_story(story)
    photos = [item["url"] for item in media if item["type"] == "photo" and item["url"]]
    videos = [item["url"] for item in media if item["type"] == "video" and item["url"]]
    has_video = any(item["type"] == "video" for item in media)
    return {
        "text": _story_text(story),
        "photos": photos,
        "videos": videos,
        "media": media,
        "has_video": has_video,
        "author": _story_author(story),
        "source_id": _story_source_id(story, expected_id),
        "content_type": content_type or ("video" if has_video else "post"),
        "expected_items": len(media),
    }


async def fetch_public_post(session, url, expected_id: str | None = None):
    target = parse_facebook_target(url)
    if target is None:
        raise ValueError('URL nao pertence ao Facebook')
    requested_id = str(expected_id or target.content_id)
    content_type = target.kind
    for _ in range(5):
        if not is_facebook_url(url):
            raise ValueError('Redirecionamento fora do Facebook')
        current_target = parse_facebook_target(url)
        if current_target:
            requested_id = requested_id or current_target.content_id
            if current_target.kind != 'unknown':
                content_type = current_target.kind
        if urlparse(url).path.rstrip('/').lower() in ('/login', '/login.php', '/checkpoint'):
            raise FacebookAccessRestricted()
        async with session.get(
            url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=25),
            headers={
                'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.7',
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 Chrome/125.0 Safari/537.36'
                ),
            },
        ) as response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('Location', '')
                if not location:
                    raise ValueError('Redirect do Facebook sem destino')
                url = urljoin(url, location)
                continue
            if response.status in (401, 403):
                raise FacebookAccessRestricted()
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError('Página do Facebook excede limite de leitura')
            html = body.decode('utf-8', errors='replace')
            canonical_url = extract_canonical_facebook_url(html)
            canonical_target = parse_facebook_target(canonical_url) if canonical_url else None
            if canonical_target:
                requested_id = requested_id or canonical_target.content_id
                if canonical_target.kind != 'unknown':
                    content_type = canonical_target.kind
            post = parse_public_post(html, requested_id, content_type)
            if post is None and requires_login(html):
                raise FacebookAccessRestricted()
            if post is not None:
                post['resolved_url'] = canonical_url or url
            return post
    raise ValueError('Muitos redirecionamentos do Facebook')


__all__ = [
    'ACCESS_NOTICE',
    'UNAVAILABLE_NOTICE',
    'FacebookAccessRestricted',
    'FacebookTarget',
    'extract_canonical_facebook_url',
    'extract_facebook_content_id',
    'fetch_public_post',
    'parse_facebook_target',
    'parse_public_post',
    'requires_login',
]
