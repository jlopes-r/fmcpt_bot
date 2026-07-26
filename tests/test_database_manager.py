import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.database import database_manager as db


class DatabaseManagerTest(unittest.TestCase):
    def test_registrar_link_e_checar_por_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", str(Path(tmp) / "links.db")):
                db.init_db()

                duplicado, info = db.registrar_link_e_checar("https://x.com/i/status/1", 10, "Ana", 1)
                self.assertFalse(duplicado)
                self.assertEqual(info, {})

                duplicado, info = db.registrar_link_e_checar("https://x.com/i/status/1", 10, "Bia", 2)
                self.assertTrue(duplicado)
                self.assertEqual(info["primeiro_user"], "Ana")
                self.assertEqual(info["vezes"], 2)

                duplicado_outro_chat, _ = db.checar_link("https://x.com/i/status/1", 20)
                self.assertFalse(duplicado_outro_chat)


if __name__ == "__main__":
    unittest.main()
