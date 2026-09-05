import unittest
import time
import json
from unittest.mock import AsyncMock, patch

import httpx

from apps.telegram_bot import instagram_extractor as ig


class FakeProfileResponse:
    status_code = 200

    def json(self):
        return {
            "data": {
                "user": {
                    "username": "openai",
                    "full_name": "OpenAI",
                    "biography": "Creating safe AGI.",
                    "edge_followed_by": {"count": 1000},
                    "edge_follow": {"count": 10},
                    "edge_owner_to_timeline_media": {"count": 42},
                    "is_private": False,
                    "is_verified": True,
                    "profile_pic_url_hd": "https://example.com/openai.jpg",
                    "external_url": "https://openai.com",
                }
            }
        }


class FakeCurrentUserResponse:
    status_code = 200

    def json(self):
        return {
            "user": {
                "pk": "12345",
                "username": "bt_mengo",
                "full_name": "Mengo",
            },
            "status": "ok",
        }


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append((url, headers))
        return FakeProfileResponse()


class FakeRateLimitedResponse:
    status_code = 429
    text = ''


class FakeHtmlProfileResponse:
    status_code = 200
    text = '''
        <meta property="og:title" content="Ada Lovelace (@ada) - Instagram">
        <meta property="og:description" content="1.2K Followers, 34 Following, 56 Posts">
        <meta property="og:image" content="https://example.com/ada.jpg">
    '''


class FakeRateLimitedProfileClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append((url, headers))
        if '/api/v1/' in url:
            return FakeRateLimitedResponse()
        if '/oembed/' in url:
            raise AssertionError('oEmbed must not be called after a 429 when HTML already gave a card')
        return FakeHtmlProfileResponse()


class FakeOEmbedProfileResponse:
    status_code = 200

    def json(self):
        return {
            'version': '1.0',
            'author_name': 'Ada Lovelace',
            'author_url': 'https://www.instagram.com/ada/',
            'author_thumbnail_url': 'https://example.com/ada_thumb.jpg',
            'title': 'Some post',
            'thumbnail_url': 'https://example.com/post.jpg',
        }


class FakeLoginPageResponse:
    status_code = 200
    text = '<html><body>Log in to Instagram</body></html>'


class FakeRateLimitedLoginProfileClient:
    """API 429 + pagina do perfil redirecionada para login (throttle real)."""

    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append((url, headers))
        if '/api/v1/' in url:
            return FakeRateLimitedResponse()
        if '/oembed/' in url:
            return FakeOEmbedProfileResponse()
        return FakeLoginPageResponse()


class FakeNewsResponse:
    status_code = 200
    url = "https://www.instagram.com/api/v1/news/inbox/"
    text = '{"counts":{"likes":0,"new_posts":0},"new_stories":[],"old_stories":[]}'

    def json(self):
        return {"counts": {"likes": 0, "new_posts": 0}, "new_stories": [], "old_stories": []}


class FakeNewsClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append((url, headers))
        return FakeNewsResponse()


class FakeLoginRequiredResponse:
    status_code = 400
    url = "https://www.instagram.com/api/v1/news/inbox/"
    text = '{"message": "login_required"}'

    def json(self):
        return {"message": "login_required"}


class FakeLoginRequiredClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        return FakeLoginRequiredResponse()


class InstagramExtractorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Testes de rede simulada não devem ler/gravar o cache real do bot.
        for name, value in (('_profile_cache', {}), ('_profile_cache_ttl', {}), ('_ig_429_since', 0.0)):
            patcher = patch.object(ig, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cache_save = patch.object(ig, '_profile_cache_save')
        self.cache_save = cache_save.start()
        self.addCleanup(cache_save.stop)

    def test_get_profile_username_only_accepts_profile_url(self):
        self.assertEqual(ig.get_profile_username("https://www.instagram.com/openai/"), "openai")
        self.assertEqual(ig.get_profile_username("https://instagram.com/user.name_123?igsh=x"), "user.name_123")
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/reel/DNBCJoiOp9J/"))
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/p/ABC123/"))
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/explore/"))
        self.assertIsNone(ig.get_profile_username("https://example.com/openai/"))
        self.assertIsNone(ig.get_profile_username("https://instagram.com.example.com/openai/"))

    async def test_fetch_instagram_profile_uses_web_profile_info(self):
        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig.httpx, "AsyncClient", FakeAsyncClient),
        ):
            profile = await ig.fetch_instagram_profile("https://www.instagram.com/openai/", "")

        self.assertEqual(profile["username"], "openai")
        self.assertEqual(profile["full_name"], "OpenAI")
        self.assertEqual(profile["followers"], 1000)
        self.assertTrue(profile["is_verified"])
        self.assertEqual(profile["profile_pic_url"], "https://example.com/openai.jpg")

    async def test_profile_429_uses_public_html_card_without_waiting_or_oembed(self):
        ig._ig_429_since = 0.0
        self.addCleanup(setattr, ig, "_ig_429_since", 0.0)
        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig.httpx, "AsyncClient", FakeRateLimitedProfileClient),
            patch.object(ig.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            profile = await ig.fetch_instagram_profile("https://www.instagram.com/ada/", "")

        self.assertTrue(profile["partial"])
        self.assertEqual(profile["username"], "ada")
        self.assertEqual(profile["full_name"], "Ada Lovelace")
        self.assertEqual(profile["followers"], "1.2K")
        self.assertEqual(profile["profile_pic_url"], "https://example.com/ada.jpg")
        self.assertIsNone(profile['biography'])
        sleep.assert_not_awaited()

    async def test_profile_429_with_login_html_still_sends_card_via_oembed(self):
        ig._ig_429_since = 0.0
        self.addCleanup(setattr, ig, "_ig_429_since", 0.0)
        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig.httpx, "AsyncClient", FakeRateLimitedLoginProfileClient),
            patch.object(ig.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            profile = await ig.fetch_instagram_profile("https://www.instagram.com/ada/", "")

        self.assertTrue(profile["partial"])
        self.assertEqual(profile["username"], "ada")
        self.assertEqual(profile["full_name"], "Ada Lovelace")
        self.assertEqual(profile["profile_pic_url"], "https://example.com/ada_thumb.jpg")
        sleep.assert_not_awaited()

    def test_mobile_profile_fields_preserve_zero_and_explicit_empty_bio(self):
        profile = ig._parse_profile_user({
            'username': 'ada', 'biography': '', 'edge_followed_by': None,
            'follower_count': 0, 'following_count': 0, 'media_count': 0,
            'clip_metadata_count': 0, 'is_private': False,
            'hd_profile_pic_url_info': {'url': 'https://example.com/hd.jpg'},
            'profile_pic_url': 'https://example.com/small.jpg',
        }, 'ADA')
        self.assertEqual(profile['biography'], '')
        self.assertEqual(profile['followers'], 0)
        self.assertEqual(profile['posts'], 0)
        self.assertEqual(profile['reels'], 0)
        self.assertFalse(profile['partial'])
        self.assertEqual(profile['profile_pic_urls'], [
            'https://example.com/hd.jpg', 'https://example.com/small.jpg',
        ])
        unknown = ig._parse_profile_user({'username': 'ada'})
        self.assertIsNone(unknown['biography'])
        self.assertIsNone(unknown['is_private'])
        self.assertTrue(unknown['partial'])

    def test_modern_html_selects_requested_account_and_decodes_nested_bio(self):
        target = {
            'username': 'lclightbox', 'full_name': 'LCsign Tony',
            'biography_with_entities': {'raw_text': 'Custom {signs} "made here"\nContact us'},
            'profile_pic_url': 'https://example.com/tony.jpg',
            'follower_count': 3000000, 'following_count': 8, 'media_count': 1481,
            'is_private': False,
        }
        payload = {'viewer': {'username': 'someone_else', 'biography': 'Wrong user'},
                   'require': [['RelayPrefetchedStreamCache', {'__bbox': {'result': {
                       'data': {'xdt_api__v1__users__web_profile_info': {'user': target}},
                   }}}]]}
        html = '<script type="application/json">' + json.dumps(payload) + '</script>'
        profile = ig._parse_profile_from_html(html, 'lclightbox')
        self.assertEqual(profile['biography'], 'Custom {signs} "made here"\nContact us')
        self.assertEqual(profile['username'], 'lclightbox')
        self.assertFalse(profile['partial'])
        self.assertIsNone(ig._parse_profile_from_html(html, 'not_in_page'))

    def test_legacy_and_serialized_html_payloads_are_supported(self):
        user = {'username': 'ada', 'biography': 'Quotes " and {braces}', 'is_private': False}
        scripts = [
            'window._sharedData = ' + json.dumps({'entry_data': {'ProfilePage': [{'graphql': {'user': user}}]}}) + ';',
            'window.__additionalDataLoaded("/ada/",' + json.dumps({'graphql': {'user': user}}) + ');',
            json.dumps({'payload': json.dumps({'user': user})}),
        ]
        for script in scripts:
            with self.subTest(script=script):
                profile = ig._parse_profile_from_html('<script>' + script + '</script>', 'ada')
                self.assertEqual(profile['biography'], user['biography'])

    def test_meta_extracts_bio_from_real_description_shape(self):
        html = '''<meta property="og:title" content="LCsign Tony (@lclightbox) • Instagram photos and videos">
        <meta property="og:description" content="3M Followers, 8 Following, 1,481 Posts - LCsign Tony (@lclightbox) on Instagram: &quot;Custom signs &amp; lighting&#10;Contact: hello@example.com&quot;">
        <meta content="https://example.com/tony.jpg?a=1&amp;b=2" property="og:image">'''
        profile = ig._parse_profile_meta(html, 'lclightbox')
        self.assertEqual(profile['full_name'], 'LCsign Tony')
        self.assertEqual(profile['biography'], 'Custom signs & lighting\nContact: hello@example.com')
        self.assertEqual(profile['posts'], '1,481')
        self.assertEqual(profile['followers'], '3M')
        self.assertEqual(profile['profile_pic_url'], 'https://example.com/tony.jpg?a=1&b=2')
        self.assertTrue(profile['partial'])

    def test_meta_rejects_wrong_account_and_generic_login(self):
        pages = [
            '<meta property="og:title" content="Instagram"><meta property="og:image" content="logo.png">',
            '<meta property="og:title" content="Another (@another) • Instagram">',
            '<meta property="og:title" content="Ada (@ada)"><meta property="og:url" content="https://www.instagram.com/another/">',
        ]
        for html in pages:
            with self.subTest(html=html):
                self.assertIsNone(ig._parse_profile_meta(html, 'ada'))

    def test_merging_does_not_replace_known_empty_bio_or_false_flags(self):
        primary = ig._parse_profile_user({'username': 'ada', 'biography': '', 'is_private': False})
        extra = {'username': 'ADA', 'biography': 'Unreliable preview', 'is_private': True,
                 'profile_pic_url': 'https://example.com/ada.jpg'}
        merged = ig._merge_profiles(primary, extra)
        self.assertEqual(merged['biography'], '')
        self.assertFalse(merged['is_private'])
        self.assertEqual(merged['profile_pic_url'], 'https://example.com/ada.jpg')
        self.assertEqual(ig._merge_profiles(primary, dict(extra, username='another')), primary)

    async def _fetch_with_responses(self, responses, username='ada'):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = responses
        with (
            patch.object(ig, '_load_cookies_from_file', return_value={}),
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig.asyncio, 'sleep', new=AsyncMock()),
        ):
            profile = await ig.fetch_instagram_profile(f'https://www.instagram.com/{username}/', '')
        return profile, client

    async def test_api_invalid_json_does_not_prevent_public_html_fallback(self):
        profile, client = await self._fetch_with_responses([
            httpx.Response(200, text='<html>Login</html>'), FakeHtmlProfileResponse(),
        ])
        self.assertEqual(profile['username'], 'ada')
        self.assertEqual(profile['profile_pic_url'], 'https://example.com/ada.jpg')
        self.assertEqual(client.get.await_count, 2)

    async def test_api_timeout_does_not_prevent_public_html_fallback(self):
        profile, client = await self._fetch_with_responses([
            httpx.ReadTimeout('API timeout'), FakeHtmlProfileResponse(),
        ])
        self.assertEqual(profile['username'], 'ada')
        self.assertEqual(client.get.await_count, 2)

    async def test_partial_api_merges_html_bio_and_meta_picture(self):
        partial_user = {'username': 'ada', 'full_name': 'Ada Lovelace',
                        'follower_count': 1234, 'following_count': 34, 'media_count': 56,
                        'is_private': False}
        html = FakeHtmlProfileResponse.text + '<script type="application/json">' + json.dumps({
            'user': {'username': 'ada', 'biography': 'Computing pioneer'},
        }) + '</script>'
        profile, client = await self._fetch_with_responses([
            httpx.Response(200, json={'data': {'user': partial_user}}), httpx.Response(200, text=html),
        ])
        self.assertEqual(profile['biography'], 'Computing pioneer')
        self.assertEqual(profile['followers'], 1234)
        self.assertEqual(profile['profile_pic_url'], 'https://example.com/ada.jpg')
        self.assertFalse(profile['partial'])
        self.assertEqual(client.get.await_count, 2)
        self.cache_save.assert_called_once()

    async def test_privacy_only_cache_does_not_suppress_profile_fetch(self):
        ig._profile_cache_upsert_privacy('openai', False)
        profile, client = await self._fetch_with_responses([FakeProfileResponse()], 'openai')
        self.assertEqual(profile['full_name'], 'OpenAI')
        self.assertEqual(client.get.await_count, 1)

    async def test_wrong_api_user_does_not_leak_into_requested_profile(self):
        profile, client = await self._fetch_with_responses([FakeProfileResponse(), FakeHtmlProfileResponse()])
        self.assertEqual(profile['username'], 'ada')
        self.assertEqual(profile['full_name'], 'Ada Lovelace')
        self.assertEqual(profile['followers'], '1.2K')
        self.assertEqual(client.get.await_count, 2)
        self.cache_save.assert_not_called()

    async def test_oembed_post_title_and_thumbnail_are_not_profile_bio_and_avatar(self):
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, json={
            'author_name': 'Ada', 'author_url': 'https://www.instagram.com/ada/',
            'title': 'A post caption', 'thumbnail_url': 'https://example.com/post.jpg',
        })
        profile = await ig._fetch_profile_via_oembed(client, 'ada')
        self.assertIsNone(profile['biography'])
        self.assertFalse(profile['profile_pic_url'])
        self.assertTrue(profile['partial'])
        self.assertIsNone(await ig._fetch_profile_via_oembed(client, 'another'))

    async def test_privacy_is_taken_only_from_requested_profile(self):
        html = '<script>' + json.dumps({'viewer': {'username': 'private_viewer', 'is_private': True},
                                       'user': {'username': 'ada', 'is_private': False}}) + '</script>'
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = httpx.Response(200, text=html, request=httpx.Request('GET', 'https://www.instagram.com/ada/'))
        with (
            patch.object(ig, '_load_cookies_from_file', return_value={}),
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
        ):
            self.assertFalse(await ig.detect_profile_privado('https://www.instagram.com/ada/'))
            self.assertIsNone(await ig.detect_profile_privado('https://www.instagram.com/unknown/'))

    async def test_validate_cookie_health_confirms_real_session(self):
        with (
            patch("apps.telegram_bot.instagram_extractor.os.path.exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc", "csrftoken": "xyz"}),
            patch.object(ig.httpx, "AsyncClient", FakeNewsClient),
        ):
            result = await ig.validate_cookie_health("C:/tmp/cookies.txt")

        self.assertTrue(result["valid"])

    async def test_validate_cookie_health_rejects_login_required(self):
        ig._cookies_known_bad = False
        with (
            patch("apps.telegram_bot.instagram_extractor.os.path.exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc", "csrftoken": "xyz"}),
            patch.object(ig.httpx, "AsyncClient", FakeLoginRequiredClient),
        ):
            result = await ig.validate_cookie_health("C:/tmp/cookies.txt")

        self.assertFalse(result["valid"])
        self.assertIn("login", result["reason"].lower())
        self.assertTrue(ig._cookies_known_bad)
        self.addCleanup(ig.reset_cookies_bad)

    async def test_reel_thumbnail_does_not_stop_video_fallback(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        thumb_result = {
            "urls": ["https://example.com/thumb.jpg"],
            "type": "photo",
            "title": "",
            "uploader": "Autor",
        }
        video_result = {
            "files": ["C:/tmp/reel.mp4"],
            "type": "video",
            "title": "Video",
            "uploader": "Autor",
        }

        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=thumb_result)) as embed,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=video_result)) as ytdlp,
        ):
            result = await ig.download_instagram(reel_url, "", "C:/tmp")

        self.assertEqual(result, video_result)
        embed.assert_awaited_once_with("DNBCJoiOp9J", {}, "reel")
        ytdlp.assert_awaited_once_with(reel_url, "", "C:/tmp")

    async def test_post_photo_can_return_embed_result(self):
        post_url = "https://www.instagram.com/p/ABC123/"
        photo_result = {
            "urls": ["https://example.com/photo.jpg"],
            "type": "photo",
            "title": "",
            "uploader": "Autor",
        }

        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=photo_result)) as embed,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock()) as ytdlp,
        ):
            result = await ig.download_instagram(post_url, "", "C:/tmp")

        self.assertEqual(result, photo_result)
        embed.assert_awaited_once_with("ABC123", {}, "p")
        ytdlp.assert_not_awaited()

    async def test_cookies_flagged_bad_still_attempts_layers(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        video_result = {
            "urls": ["https://example.com/reel.mp4"],
            "type": "video",
            "title": "Reel",
            "uploader": "Autor",
        }

        ig._cookies_known_bad = True
        ig._cookies_bad_since = time.time()
        self.addCleanup(ig.reset_cookies_bad)

        with (
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc"}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=video_result)),
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock()) as ytdlp,
        ):
            result = await ig.download_instagram(reel_url, "C:/tmp/cookies.txt", "C:/tmp")

        self.assertEqual(result, video_result)
        ytdlp.assert_not_awaited()
        self.assertFalse(ig._cookies_known_bad)

    async def test_reel_ytdlp_empty_caption_is_enriched_via_oembed(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        video_result = {
            "files": ["C:/tmp/reel.mp4"],
            "type": "video",
            "title": "",
            "uploader": "Autor",
        }

        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=video_result)) as ytdlp,
            patch.object(ig, "_fetch_post_meta_via_oembed", new=AsyncMock(return_value={
                'title': '🐺 lobo na rua',
                'uploader': 'gustramontini',
            })) as oembed,
        ):
            result = await ig.download_instagram(reel_url, "", "C:/tmp")

        self.assertEqual(result["title"], '🐺 lobo na rua')
        self.assertEqual(result["uploader"], "gustramontini")
        self.assertEqual(result["files"], ["C:/tmp/reel.mp4"])
        ytdlp.assert_awaited_once_with(reel_url, "", "C:/tmp")
        oembed.assert_awaited_once_with("DNBCJoiOp9J", "reel")

    async def test_reel_with_caption_does_not_call_oembed(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        video_result = {
            "files": ["C:/tmp/reel.mp4"],
            "type": "video",
            "title": "Legenda ja presente",
            "uploader": "Autor",
        }

        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=video_result)),
            patch.object(ig, "_fetch_post_meta_via_oembed", new=AsyncMock()) as oembed,
        ):
            result = await ig.download_instagram(reel_url, "", "C:/tmp")

        self.assertEqual(result["title"], "Legenda ja presente")
        oembed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
