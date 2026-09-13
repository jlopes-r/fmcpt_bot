import unittest
import time
import json
import tempfile
from pathlib import Path
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
    """API 429 seguido de cooldown: rotas web do www sao ignoradas.

    O unico recurso permitido apos o 429 e o oEmbed (host separado).
    """

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
        raise AssertionError('HTML must not be fetched after a 429: ' + url)


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
        for name, value in (
            ('_profile_cache', {}), ('_profile_cache_ttl', {}),
            ('_ig_429_since', 0.0), ('_ig_last_request_at', 0.0),
        ):
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

    def test_story_links_with_and_without_media_id_are_recognized(self):
        self.assertTrue(ig._is_story("https://www.instagram.com/stories/ada/123456/"))
        self.assertEqual(ig._get_story_info(
            "https://www.instagram.com/stories/ada/123456/"
        ), ("ada", "123456"))
        self.assertTrue(ig._is_story("https://www.instagram.com/stories/ada/"))
        self.assertEqual(ig._get_story_info(
            "https://www.instagram.com/stories/ada/"
        ), ("ada", None))
        self.assertFalse(ig._is_story(
            "https://www.instagram.com/stories/highlights/987654/"
        ))

    async def test_current_graphql_response_parses_carousel(self):
        product = {
            'user': {'username': 'ada'},
            'caption': {'text': 'Album'},
            'carousel_media': [
                {'image_versions2': {'candidates': [{'url': 'https://cdn/one.jpg'}]}},
                {'video_versions': [{'url': 'https://cdn/two.mp4'}]},
            ],
        }
        response = httpx.Response(
            200,
            json={'data': {'xig_polaris_media': {
                'if_not_gated_logged_out': product,
            }}},
            request=httpx.Request('POST', 'https://www.instagram.com/api/graphql'),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with (
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig, '_ig_wait_pacing', new=AsyncMock()),
        ):
            result = await ig._extract_via_graphql('ABC123', {'csrftoken': 'csrf'})

        self.assertEqual(result['urls'], ['https://cdn/one.jpg', 'https://cdn/two.mp4'])
        self.assertEqual(result['type'], 'carousel')
        client.get.assert_not_awaited()

    async def test_highlight_api_collects_all_items_in_order(self):
        response = httpx.Response(
            200,
            json={'reels': {'highlight:987654': {'items': [
                {
                    'user': {'username': 'ada'},
                    'video_versions': [{'url': 'https://cdn/one.mp4'}],
                },
                {
                    'user': {'username': 'ada'},
                    'image_versions2': {'candidates': [{'url': 'https://cdn/two.jpg'}]},
                },
            ]}}},
            request=httpx.Request('GET', 'https://i.instagram.com/api/v1/feed/reels_media/'),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with (
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig, '_ig_wait_pacing', new=AsyncMock()),
        ):
            result = await ig._extract_via_highlights_api(
                '987654', {'sessionid': 'session', 'csrftoken': 'csrf'}
            )

        self.assertEqual(result['urls'], ['https://cdn/one.mp4', 'https://cdn/two.jpg'])
        self.assertEqual(result['type'], 'carousel')

    async def test_direct_story_api_collects_every_item_in_order(self):
        response = httpx.Response(
            200,
            json={'reels': {'42': {
                'user': {'username': 'ada'},
                'items': [
                    {
                        'pk': 'one',
                        'user': {'username': 'ada'},
                        'image_versions2': {'candidates': [{'url': 'https://cdn/one.jpg'}]},
                    },
                    {
                        'pk': 'two',
                        'user': {'username': 'ada'},
                        'video_versions': [{'url': 'https://cdn/two.mp4'}],
                    },
                ],
            }}},
            request=httpx.Request('GET', 'https://i.instagram.com/api/v1/feed/reels_media/'),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with (
            patch.object(ig, '_resolve_instagram_user_id', new=AsyncMock(return_value='42')),
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig, '_ig_wait_pacing', new=AsyncMock()),
        ):
            result = await ig._extract_via_stories_api('ada', {'sessionid': 'session'})

        self.assertEqual(result['urls'], ['https://cdn/one.jpg', 'https://cdn/two.mp4'])
        self.assertEqual(result['_expected_items'], 2)
        self.assertTrue(result['_complete'])
        self.assertTrue(result['_story_sequence'])
        self.assertIn('www.instagram.com', client.get.await_args.args[0])

    async def test_story_media_api_tries_web_endpoint_before_mobile(self):
        response = httpx.Response(
            200,
            json={'items': [{
                'pk': '123456',
                'user': {'username': 'ada'},
                'video_versions': [{'url': 'https://cdn/story.mp4'}],
            }]},
            request=httpx.Request(
                'GET',
                'https://www.instagram.com/api/v1/media/123456/info/',
            ),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        with (
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig, '_ig_wait_pacing', new=AsyncMock()),
        ):
            result = await ig._extract_via_api_media_id(
                '123456', {'sessionid': 'session'}
            )

        self.assertEqual(result['urls'], ['https://cdn/story.mp4'])
        self.assertEqual(client.get.await_count, 1)
        self.assertIn('www.instagram.com', client.get.await_args.args[0])

    async def test_story_sequence_can_select_exact_media_id(self):
        payload = {'reels': {'42': {
            'user': {'username': 'ada'},
            'items': [
                {
                    'pk': 'one',
                    'image_versions2': {'candidates': [{'url': 'https://cdn/one.jpg'}]},
                },
                {
                    'pk': 'two',
                    'video_versions': [{'url': 'https://cdn/two.mp4'}],
                },
            ],
        }}}

        result = ig._parse_story_reels(payload, 'ada', 'two')

        self.assertEqual(result['urls'], ['https://cdn/two.mp4'])
        self.assertEqual(result['_expected_items'], 1)
        self.assertFalse(result['_story_sequence'])

    def test_api_carousel_rejects_missing_child_media(self):
        item = {
            'carousel_media_count': 2,
            'carousel_media': [
                {'image_versions2': {'candidates': [{'url': 'https://cdn/one.jpg'}]}},
                {'image_versions2': {'candidates': []}},
            ],
        }
        self.assertIsNone(ig._parse_api_item(item))

    def test_graphql_carousel_rejects_truncated_page(self):
        media = {
            'edge_sidecar_to_children': {
                'count': 3,
                'page_info': {'has_next_page': True},
                'edges': [
                    {'node': {'display_url': 'https://cdn/one.jpg'}},
                    {'node': {'display_url': 'https://cdn/two.jpg'}},
                ],
            },
        }
        self.assertIsNone(ig._parse_graphql_media(media))

    def test_story_sequence_rejects_partial_payload(self):
        payload = {'reels': {'42': {'user': {'username': 'ada'}, 'items': [
            {'pk': 'one', 'image_versions2': {'candidates': [{'url': 'https://cdn/one.jpg'}]}},
            {'pk': 'two'},
        ]}}}
        self.assertIsNone(ig._parse_story_reels(payload, 'ada'))

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

    async def test_profile_429_skips_web_layers_and_uses_oembed_card(self):
        ig._ig_429_since = 0.0
        self.addCleanup(setattr, ig, "_ig_429_since", 0.0)
        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig.httpx, "AsyncClient", FakeRateLimitedProfileClient),
            patch.object(ig.asyncio, "sleep", new=AsyncMock()),
        ):
            profile = await ig.fetch_instagram_profile("https://www.instagram.com/ada/", "")

        self.assertTrue(profile["partial"])
        self.assertEqual(profile["username"], "ada")
        self.assertEqual(profile["full_name"], "Ada Lovelace")
        self.assertEqual(profile["profile_pic_url"], "https://example.com/ada_thumb.jpg")

    async def test_profile_cooldown_skips_all_web_calls_and_only_uses_oembed(self):
        ig._ig_429_since = 9999999999.0
        self.addCleanup(setattr, ig, "_ig_429_since", 0.0)
        with (
            patch.object(ig, "_load_cookies_from_file", return_value={}),
            patch.object(ig.httpx, "AsyncClient", FakeRateLimitedProfileClient),
            patch.object(ig.asyncio, "sleep", new=AsyncMock()),
        ):
            profile = await ig.fetch_instagram_profile("https://www.instagram.com/ada/", "")

        self.assertTrue(profile["partial"])
        self.assertEqual(profile["username"], "ada")
        self.assertEqual(profile["full_name"], "Ada Lovelace")
        self.assertEqual(profile["profile_pic_url"], "https://example.com/ada_thumb.jpg")

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
        self.assertEqual(result["failure"], "cookie_invalid")
        self.assertFalse(ig._cookies_known_bad)
        self.addCleanup(ig.reset_cookies_bad)

    async def test_validate_cookie_health_does_not_invalidate_on_429(self):
        ig.reset_cookies_bad()
        self.addCleanup(ig.reset_cookies_bad)
        with (
            patch("apps.telegram_bot.instagram_extractor.os.path.exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc"}),
            patch.object(ig.httpx, "AsyncClient", FakeRateLimitedProfileClient),
        ):
            result = await ig.validate_cookie_health("C:/tmp/cookies.txt")

        self.assertFalse(result["valid"])
        self.assertTrue(result["rate_limited"])
        self.assertFalse(ig._cookies_known_bad)

    async def test_primary_cookie_success_does_not_try_secondary(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        video_result = {
            "urls": ["https://example.com/reel.mp4"],
            "type": "video",
            "title": "Reel",
            "uploader": "Autor",
        }

        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc"}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=video_result)) as api,
        ):
            result = await ig.download_instagram(
                reel_url, "primary.txt", "C:/tmp", secondary_cookie_path="secondary.txt"
            )

        self.assertEqual(result["_cookie_source"], "primary")
        self.assertFalse(result["_primary_cookie_failed"])
        self.assertEqual(api.await_count, 1)

    async def test_secondary_cookie_is_used_after_primary_failure(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        video_result = {
            "urls": ["https://example.com/reel.mp4"],
            "type": "video",
            "title": "Video",
            "uploader": "Autor",
        }

        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", side_effect=[
                {"sessionid": "primary"}, {"sessionid": "secondary"},
            ]),
            patch.object(ig, "_extract_via_api", new=AsyncMock(side_effect=[None, video_result])) as api,
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=None)),
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=None)),
        ):
            result = await ig.download_instagram(
                reel_url, "primary.txt", "C:/tmp", secondary_cookie_path="secondary.txt"
            )

        self.assertEqual(result["_cookie_source"], "secondary")
        self.assertTrue(result["_primary_cookie_failed"])
        self.assertEqual(api.await_count, 2)

    async def test_authenticated_layers_and_ytdlp_use_each_cookie(self):
        reel_url = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=None)) as api,
            patch.object(ig, "_extract_via_graphql", new=AsyncMock(return_value=None)) as graphql,
            patch.object(ig, "_extract_via_embed", new=AsyncMock(return_value=None)) as embed,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=None)) as ytdlp,
        ):
            result = await ig.download_instagram(
                reel_url, "primary.txt", "C:/tmp", secondary_cookie_path="secondary.txt"
            )

        self.assertIsNone(result)
        self.assertEqual(api.await_count, 2)
        self.assertEqual(graphql.await_count, 2)
        self.assertEqual(embed.await_count, 2)
        self.assertEqual(ytdlp.await_count, 2)
        self.assertEqual(ytdlp.await_args_list[0].args[1], "primary.txt")
        self.assertEqual(ytdlp.await_args_list[1].args[1], "secondary.txt")

    async def test_story_without_media_id_falls_back_to_authenticated_ytdlp(self):
        story_url = "https://www.instagram.com/stories/ada/"
        story_result = {
            "files": ["C:/tmp/story.mp4"],
            "type": "video",
            "title": "Story by ada",
            "uploader": "ada",
        }
        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(ig, "_extract_via_api_media_id", new=AsyncMock()) as media_api,
            patch.object(ig, "_extract_via_stories_api", new=AsyncMock(return_value=None)) as stories_api,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock(return_value=story_result)) as ytdlp,
        ):
            result = await ig.download_instagram(story_url, "primary.txt", "C:/tmp")

        media_api.assert_not_awaited()
        stories_api.assert_awaited_once_with("ada", {"sessionid": "x"})
        ytdlp.assert_awaited_once_with(story_url, "primary.txt", "C:/tmp")
        self.assertEqual(result["_cookie_source"], "primary")

    async def test_story_without_id_uses_complete_direct_sequence(self):
        story_url = "https://www.instagram.com/stories/ada/"
        story_result = {
            "urls": ["https://cdn/first.jpg", "https://cdn/second.mp4"],
            "type": "carousel",
            "title": "",
            "uploader": "ada",
            "_expected_items": 2,
            "_complete": True,
            "_story_sequence": True,
        }
        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(ig, "_extract_via_stories_api", new=AsyncMock(return_value=story_result)) as stories_api,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock()) as ytdlp,
        ):
            result = await ig.download_instagram(story_url, "primary.txt", "C:/tmp")

        stories_api.assert_awaited_once_with("ada", {"sessionid": "x"})
        ytdlp.assert_not_awaited()
        self.assertEqual(result["urls"], story_result["urls"])
        self.assertTrue(result["_complete"])

    async def test_share_link_is_resolved_before_routing(self):
        shared = "https://www.instagram.com/share/reel/example/"
        canonical = "https://www.instagram.com/reel/DNBCJoiOp9J/"
        result_data = {
            "urls": ["https://cdn/reel.mp4"],
            "type": "video",
            "title": "",
            "uploader": "ada",
        }
        with (
            patch.object(ig, "resolve_instagram_share_url", new=AsyncMock(return_value=canonical)) as resolver,
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(ig, "_extract_via_api", new=AsyncMock(return_value=result_data)),
        ):
            result = await ig.download_instagram(shared, "primary.txt", "C:/tmp")

        resolver.assert_awaited_once_with(shared)
        self.assertEqual(result["urls"], ["https://cdn/reel.mp4"])

    async def test_ytdlp_fallback_uses_isolated_worker_and_keeps_story_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cookie = root / "cookies.txt"
            cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            first = root / "first.mp4"
            second = root / "second.jpg"
            first.write_bytes(b"video")
            second.write_bytes(b"photo")
            worker_result = {
                "title": "Stories",
                "uploader": "ada",
                "entries": [
                    {"requested_downloads": [{"filepath": str(first)}]},
                    {"filepath": str(second)},
                ],
            }
            worker = AsyncMock(return_value=worker_result)
            with patch.object(ig, "baixar_com_ytdlp", new=worker):
                result = await ig._extract_via_ytdlp(
                    "https://www.instagram.com/stories/ada/",
                    str(cookie),
                    folder,
                )

        self.assertEqual(result["files"], [str(first), str(second)])
        options = worker.await_args.args[1]
        self.assertFalse(options["noplaylist"])
        self.assertGreaterEqual(options["playlistend"], 2)
        self.assertGreaterEqual(worker.await_args.kwargs["timeout"], 30)

    async def test_story_with_media_id_uses_direct_authenticated_api(self):
        story_url = "https://www.instagram.com/stories/ada/123456/"
        story_result = {
            "urls": ["https://cdn/story.jpg"],
            "type": "photo",
            "title": "",
            "uploader": "ada",
        }
        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(ig, "_extract_via_api_media_id", new=AsyncMock(return_value=story_result)) as media_api,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock()) as ytdlp,
        ):
            result = await ig.download_instagram(story_url, "primary.txt", "C:/tmp")

        media_api.assert_awaited_once_with("123456", {"sessionid": "x"})
        ytdlp.assert_not_awaited()
        self.assertEqual(result["_cookie_source"], "primary")

    async def test_story_with_media_id_falls_back_to_exact_story_feed(self):
        story_url = "https://www.instagram.com/stories/ada/123456/"
        story_result = {
            "urls": ["https://cdn/story.jpg"],
            "type": "photo",
            "title": "",
            "uploader": "ada",
            "_expected_items": 1,
            "_complete": True,
        }
        with (
            patch.object(ig.os.path, "exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "x"}),
            patch.object(
                ig,
                "_extract_via_api_media_id",
                new=AsyncMock(return_value=None),
            ) as media_api,
            patch.object(
                ig,
                "_extract_via_stories_api",
                new=AsyncMock(return_value=story_result),
            ) as stories_api,
            patch.object(ig, "_extract_via_ytdlp", new=AsyncMock()) as ytdlp,
        ):
            result = await ig.download_instagram(
                story_url, "primary.txt", "C:/tmp"
            )

        media_api.assert_awaited_once_with("123456", {"sessionid": "x"})
        stories_api.assert_awaited_once_with(
            "ada", {"sessionid": "x"}, "123456"
        )
        ytdlp.assert_not_awaited()
        self.assertEqual(result["urls"], ["https://cdn/story.jpg"])

    async def test_profile_tries_secondary_cookie_after_primary_session_fails(self):
        failed = httpx.Response(401, text='login required')
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = [failed, failed, FakeProfileResponse()]
        with (
            patch.object(ig, '_load_cookies_from_file', side_effect=[
                {'sessionid': 'primary'}, {'sessionid': 'secondary'},
            ]),
            patch.object(ig.httpx, 'AsyncClient', return_value=client),
            patch.object(ig.asyncio, 'sleep', new=AsyncMock()),
        ):
            profile = await ig.fetch_instagram_profile(
                'https://www.instagram.com/openai/',
                'primary.txt',
                secondary_cookie_path='secondary.txt',
            )

        self.assertEqual(profile['username'], 'openai')
        self.assertEqual(client.get.await_count, 3)
        secondary_headers = client.get.await_args_list[2].kwargs['headers']
        self.assertIn('sessionid=secondary', secondary_headers['Cookie'])


if __name__ == "__main__":
    unittest.main()
