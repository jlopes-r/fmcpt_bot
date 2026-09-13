import unittest

import httpx

from apps.telegram_bot.instagram_resilience import (
    InstagramAccountPool,
    InstagramFailure,
    classify_instagram_response,
    load_graphql_documents,
    resolve_instagram_share_url,
)


class InstagramShareResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_non_share_url_never_touches_network(self):
        async def handler(_request):
            raise AssertionError("network should not be used")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await resolve_instagram_share_url(
                "https://www.instagram.com/reel/ABC123/", client=client
            )
        self.assertEqual(result, "https://www.instagram.com/reel/ABC123/")

    async def test_share_redirects_are_resolved_hop_by_hop(self):
        visited = []

        async def handler(request):
            visited.append(str(request.url))
            if request.url.path.startswith("/share/"):
                return httpx.Response(302, headers={"Location": "/reel/ABC123/"})
            return httpx.Response(200, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await resolve_instagram_share_url(
                "https://www.instagram.com/share/reel/xyz/", client=client
            )

        self.assertEqual(result, "https://www.instagram.com/reel/ABC123/")
        self.assertEqual(len(visited), 2)

    async def test_external_redirect_is_rejected_before_following(self):
        visited = []

        async def handler(request):
            visited.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "hosts oficiais"):
                await resolve_instagram_share_url(
                    "https://instagram.com/share/reel/xyz/", client=client
                )
        self.assertEqual(len(visited), 1)

    async def test_lookalike_initial_host_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "hosts oficiais"):
            await resolve_instagram_share_url(
                "https://instagram.com.evil.example/share/reel/xyz/"
            )


class InstagramFailureClassificationTest(unittest.TestCase):
    @staticmethod
    def response(status, *, text="", url="https://www.instagram.com/api/v1/"):
        return httpx.Response(
            status,
            text=text,
            request=httpx.Request("GET", url),
        )

    def test_cookie_challenge_and_ip_limit_are_distinct(self):
        self.assertEqual(
            classify_instagram_response(self.response(401)),
            InstagramFailure.COOKIE_INVALID,
        )
        self.assertEqual(
            classify_instagram_response(
                self.response(400, text='{"message":"checkpoint_required"}')
            ),
            InstagramFailure.CHALLENGE,
        )
        self.assertEqual(
            classify_instagram_response(self.response(429, text="login_required")),
            InstagramFailure.IP_RATE_LIMITED,
        )


class InstagramAccountPoolTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.pool = InstagramAccountPool(
            clock=lambda: self.now,
            failure_threshold=2,
            circuit_seconds=30,
            invalid_cookie_seconds=60,
            challenge_seconds=120,
            ip_rate_limit_seconds=20,
        )
        self.primary = self.pool.register("primary", "primary.txt")
        self.secondary = self.pool.register("secondary", "secondary.txt")

    def test_invalid_primary_does_not_block_secondary(self):
        self.pool.report_failure(
            self.primary,
            "media_api",
            InstagramFailure.COOKIE_INVALID,
            "login_required",
        )
        self.assertFalse(self.pool.can_attempt(self.primary, "media_api"))
        self.assertTrue(self.pool.can_attempt(self.secondary, "media_api"))
        self.assertEqual(self.pool.health(self.primary).state, "cookie_invalid")

    def test_endpoint_circuit_opens_only_after_threshold(self):
        self.pool.report_failure(
            self.primary, "graphql", InstagramFailure.TRANSIENT, "timeout"
        )
        self.assertTrue(self.pool.can_attempt(self.primary, "graphql"))
        self.pool.report_failure(
            self.primary, "graphql", InstagramFailure.TRANSIENT, "timeout"
        )
        self.assertFalse(self.pool.can_attempt(self.primary, "graphql"))
        self.assertTrue(self.pool.can_attempt(self.primary, "media_api"))
        self.now += 31
        self.assertTrue(self.pool.can_attempt(self.primary, "graphql"))

    def test_ip_limit_blocks_web_for_all_accounts_but_not_ytdlp(self):
        self.pool.report_failure(
            self.primary,
            "story_api",
            InstagramFailure.IP_RATE_LIMITED,
            "429",
        )
        self.assertFalse(self.pool.can_attempt(self.secondary, "story_api"))
        self.assertTrue(self.pool.can_attempt(self.secondary, "ytdlp"))
        self.now += 21
        self.assertTrue(self.pool.can_attempt(self.secondary, "story_api"))


class GraphQLDocumentConfigTest(unittest.TestCase):
    def test_named_version_can_be_overridden_with_valid_ids(self):
        documents = load_graphql_documents({
            "IG_GRAPHQL_DOCSET": "polaris-2025",
            "IG_GRAPHQL_MODERN_DOC_ID": "12345678901",
            "IG_GRAPHQL_LEGACY_DOC_IDS": '["22222222222", "bad", "12345678901"]',
        })
        self.assertEqual(documents.version, "polaris-2025")
        self.assertEqual(documents.modern, "12345678901")
        self.assertEqual(documents.legacy, ("22222222222",))

    def test_invalid_override_falls_back_to_versioned_default(self):
        documents = load_graphql_documents({
            "IG_GRAPHQL_DOCSET": "unknown",
            "IG_GRAPHQL_MODERN_DOC_ID": "not-a-number",
        })
        self.assertEqual(documents.modern, "27130156389949648")


if __name__ == "__main__":
    unittest.main()
