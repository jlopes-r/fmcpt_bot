import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from apps.telegram_bot import social_contracts as contracts
from apps.telegram_bot.models.media import MediaBundle, MediaItem


class ContractConfigurationTests(unittest.TestCase):
    def test_parses_legacy_named_https_urls(self):
        targets = contracts.parse_contract_targets(
            '{"instagram_post":"https://www.instagram.com/p/abc/",'
            '"x":"https://x.com/example/status/123"}'
        )
        self.assertEqual([target.name for target in targets], ["instagram_post", "x"])
        self.assertEqual(targets[1].platform, "twitter")

    def test_parses_result_and_quote_expectations(self):
        [target] = contracts.parse_contract_targets(
            '{"x_quote":{"url":"https://x.com/example/status/123",'
            '"min_items":0,"require_text":true,'
            '"quote":{"min_items":1,"kinds":["video"]}}}'
        )
        self.assertEqual(target.expectation.min_items, 0)
        self.assertTrue(target.expectation.require_text)
        self.assertEqual(target.quote, contracts.BundleExpectation(1, ("video",)))

    def test_rejects_unknown_domain_and_non_https(self):
        with self.assertRaisesRegex(ValueError, "nao permitida"):
            contracts.parse_contract_targets('{"bad":"https://example.com/post/1"}')
        with self.assertRaisesRegex(ValueError, "nao permitida"):
            contracts.parse_contract_targets('{"bad":"http://x.com/user/status/1"}')

    def test_rejects_platform_mismatch_and_invalid_kind(self):
        with self.assertRaisesRegex(ValueError, "nao corresponde"):
            contracts.parse_contract_targets(
                '{"bad":{"url":"https://x.com/user/status/1",'
                '"platform":"instagram"}}'
            )
        with self.assertRaisesRegex(ValueError, "tipo invalido"):
            contracts.parse_contract_targets(
                '{"bad":{"url":"https://x.com/user/status/1",'
                '"kinds":["archive"]}}'
            )

    def test_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            contracts.parse_contract_targets('["https://x.com/user/status/1"]')


class ContractRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_instagram_profile_checks_expected_username(self):
        target = contracts.ContractTarget("profile", "https://instagram.com/example/")
        with patch.object(
            contracts,
            "fetch_instagram_profile",
            new=AsyncMock(return_value={"username": "example"}),
        ):
            result = await contracts.check_contract_target(target)
        self.assertTrue(result.ok)
        self.assertIn("instagram/profile", result.detail)

    async def test_contract_uses_production_registry_and_prepares_media(self):
        bundle = MediaBundle(
            "twitter",
            (MediaItem("https://cdn.example/photo.jpg", "photo"),),
            text="texto do tweet",
            source_id="123",
            expected_items=1,
        )
        registry = unittest.mock.Mock()
        registry.extract = AsyncMock(return_value=bundle)
        prepare = AsyncMock(return_value=bundle)
        target = contracts.ContractTarget(
            "x_photo",
            "https://x.com/example/status/123",
            platform="twitter",
            expectation=contracts.BundleExpectation(
                min_items=1,
                kinds=("photo",),
                require_text=True,
            ),
        )
        with (
            patch.object(contracts, "build_default_registry", return_value=registry),
            patch.object(contracts.MediaSender, "prepare", new=prepare),
        ):
            result = await contracts.check_contract_target(target)
        self.assertTrue(result.ok)
        registry.extract.assert_awaited_once()
        prepare.assert_awaited_once()

    async def test_contract_rejects_partial_bundle(self):
        bundle = MediaBundle(
            "instagram",
            (MediaItem("https://cdn.example/photo.jpg", "photo"),),
            expected_items=2,
            metadata={"content_type": "post"},
        )
        registry = unittest.mock.Mock()
        registry.extract = AsyncMock(return_value=bundle)
        target = contracts.ContractTarget(
            "carousel",
            "https://instagram.com/p/abc/",
            platform="instagram",
            content_type="post",
        )
        with (
            patch.object(contracts, "build_default_registry", return_value=registry),
            patch.object(
                contracts.MediaSender,
                "prepare",
                new=AsyncMock(return_value=bundle),
            ),
        ):
            result = await contracts.check_contract_target(target)
        self.assertFalse(result.ok)
        self.assertIn("parcial", result.detail)

    async def test_contract_validates_quoted_tweet(self):
        quote = MediaBundle(
            "twitter",
            (MediaItem("https://cdn.example/video.mp4", "video"),),
            expected_items=1,
        )
        bundle = MediaBundle(
            "twitter",
            (),
            text="comentario",
            metadata={"quote": quote},
        )
        registry = unittest.mock.Mock()
        registry.extract = AsyncMock(return_value=bundle)
        target = contracts.ContractTarget(
            "x_quote",
            "https://x.com/example/status/123",
            platform="twitter",
            expectation=contracts.BundleExpectation(min_items=0, require_text=True),
            quote=contracts.BundleExpectation(min_items=1, kinds=("video",)),
        )

        async def prepared(value, _directory):
            return value

        with (
            patch.object(contracts, "build_default_registry", return_value=registry),
            patch.object(contracts.MediaSender, "prepare", side_effect=prepared),
        ):
            result = await contracts.check_contract_target(target)
        self.assertTrue(result.ok)
        self.assertIn("citado=1", result.detail)

    async def test_runner_reports_timeout_and_continues(self):
        targets = [
            contracts.ContractTarget("slow", "https://x.com/a/status/1"),
            contracts.ContractTarget("next", "https://x.com/a/status/2"),
        ]

        async def check(target, **_kwargs):
            if target.name == "slow":
                await asyncio.sleep(0.05)
            return contracts.ContractResult(target.name, True, "ok")

        with patch.object(contracts, "check_contract_target", side_effect=check):
            results = await contracts.run_contract_checks(targets, timeout=0.01)
        self.assertFalse(results[0].ok)
        self.assertTrue(results[1].ok)


if __name__ == "__main__":
    unittest.main()
