import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from apps.telegram_bot import social_contracts as contracts


class ContractConfigurationTests(unittest.TestCase):
    def test_parses_named_https_urls(self):
        targets = contracts.parse_contract_targets(
            '{"instagram_post":"https://www.instagram.com/p/abc/",'
            '"x":"https://x.com/example/status/123"}'
        )
        self.assertEqual([target.name for target in targets], ["instagram_post", "x"])

    def test_rejects_unknown_domain_and_non_https(self):
        with self.assertRaisesRegex(ValueError, "nao permitida"):
            contracts.parse_contract_targets('{"bad":"https://example.com/post/1"}')
        with self.assertRaisesRegex(ValueError, "nao permitida"):
            contracts.parse_contract_targets('{"bad":"http://x.com/user/status/1"}')

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

    async def test_instagram_media_requires_configured_cookies(self):
        target = contracts.ContractTarget("post", "https://instagram.com/p/abc/")
        result = await contracts.check_contract_target(target)
        self.assertFalse(result.ok)
        self.assertIn("cookies", result.detail)

    async def test_generic_contract_uses_metadata_without_download(self):
        target = contracts.ContractTarget("x", "https://x.com/example/status/123")
        with patch.object(contracts, "_generic_metadata", return_value={"id": "123"}) as extract:
            result = await contracts.check_contract_target(target)
        self.assertTrue(result.ok)
        extract.assert_called_once_with(target.url)

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
