import hashlib
import hmac
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.mini_app_server import server
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

    def test_load_catalog_payload_overlays_custom_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_file = root / "catalog.json"
            commands_file = root / "comandos.json"
            categories_file = root / "categorias.json"
            catalog_file.write_text(
                json.dumps({"bots": [{"id": "comandos", "commands": []}]}),
                encoding="utf-8",
            )
            commands_file.write_text(
                json.dumps({"sorry": {"tipo": "texto", "conteudo": "foi mal"}}),
                encoding="utf-8",
            )
            categories_file.write_text(json.dumps(["Memes"]), encoding="utf-8")

            with patch.object(server, "CATALOG_FILE", catalog_file), \
                 patch.object(server, "COMANDOS_FILE", commands_file), \
                 patch.object(server, "CUSTOM_CATEGORIES_FILE", categories_file):
                payload = server.load_catalog_payload()

        comandos_bot = payload["bots"][0]
        self.assertIn("sorry", comandos_bot["customCommands"])
        self.assertEqual(comandos_bot["customCategories"], ["Memes"])

    def test_validate_command_payload_requires_text_content_for_text_create(self):
        ok, error = server.validate_command_payload(
            {"name": "ola", "type": "texto", "description": "Oi"},
            creating=True,
        )

        self.assertFalse(ok)
        self.assertIn("Conteudo", error)

    def test_build_command_record_keeps_uploaded_media_private_path(self):
        record = server.build_command_record(
            {
                "name": "foto",
                "type": "foto",
                "description": "Uma foto",
                "category": "Memes",
                "content": "legenda",
            },
            {"id": 123},
            upload_path="/srv/fmcpt/data/custom_command_uploads/file.jpg",
        )

        self.assertEqual(record["tipo"], "foto")
        self.assertIsNone(record["media_id"])
        self.assertEqual(record["media_path"], "/srv/fmcpt/data/custom_command_uploads/file.jpg")


if __name__ == "__main__":
    unittest.main()
