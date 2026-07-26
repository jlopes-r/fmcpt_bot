import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from apps.mini_app_server.server import validate_init_data


def signed_init_data(token: str, payload: dict) -> str:
    pairs = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps(payload, separators=(",", ":")),
    }
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(pairs)


class MiniAppServerTest(unittest.TestCase):
    def test_validate_init_data_accepts_signed_telegram_payload(self):
        token = "123456:ABC"
        init_data = signed_init_data(token, {"id": 42, "first_name": "Ana"})

        self.assertEqual(validate_init_data(init_data, token)["id"], 42)

    def test_validate_init_data_rejects_wrong_token(self):
        init_data = signed_init_data("123456:ABC", {"id": 42})

        self.assertIsNone(validate_init_data(init_data, "wrong-token"))


if __name__ == "__main__":
    unittest.main()
