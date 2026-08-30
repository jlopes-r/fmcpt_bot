import unittest
import time
from unittest.mock import AsyncMock, patch

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


class FakeCurrentUserResponse:
    status_code = 200
    url = "https://www.instagram.com/api/v1/accounts/current_user/?edit=true"
    text = '{"user": {"pk": "12345", "username": "bt_mengo", "full_name": "Mengo"}, "status": "ok"}'

    def json(self):
        return {
            "user": {
                "pk": "12345",
                "username": "bt_mengo",
                "full_name": "Mengo",
            },
            "status": "ok",
        }


class FakeCurrentUserClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append((url, headers))
        return FakeCurrentUserResponse()


class FakeLoginRequiredResponse:
    status_code = 400
    url = "https://www.instagram.com/api/v1/accounts/current_user/?edit=true"
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
    def test_get_profile_username_only_accepts_profile_url(self):
        self.assertEqual(ig.get_profile_username("https://www.instagram.com/openai/"), "openai")
        self.assertEqual(ig.get_profile_username("https://instagram.com/user.name_123?igsh=x"), "user.name_123")
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/reel/DNBCJoiOp9J/"))
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/p/ABC123/"))
        self.assertIsNone(ig.get_profile_username("https://www.instagram.com/explore/"))

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

    async def test_validate_cookie_health_confirms_real_session(self):
        with (
            patch("apps.telegram_bot.instagram_extractor.os.path.exists", return_value=True),
            patch.object(ig, "_load_cookies_from_file", return_value={"sessionid": "abc", "csrftoken": "xyz"}),
            patch.object(ig.httpx, "AsyncClient", FakeCurrentUserClient),
        ):
            result = await ig.validate_cookie_health("C:/tmp/cookies.txt")

        self.assertTrue(result["valid"])
        self.assertEqual(result["username"], "bt_mengo")
        self.assertEqual(result["user_id"], "12345")

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


if __name__ == "__main__":
    unittest.main()
