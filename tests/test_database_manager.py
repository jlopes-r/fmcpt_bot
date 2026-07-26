import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from packages.database import database_manager as db


def fixed_datetime(year, month, day, hour=12):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(year, month, day, hour, tzinfo=tz)

    return FixedDateTime


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

    def test_boca_de_leite_so_dispara_para_repetido_no_mesmo_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", str(Path(tmp) / "links.db")):
                db.init_db()

                with patch.object(db, "datetime", fixed_datetime(2026, 7, 10)):
                    duplicado, _ = db.registrar_link_e_checar("https://instagram.com/reel/abc", 10, "Ana", 1)
                    self.assertFalse(duplicado)

                with patch.object(db, "datetime", fixed_datetime(2026, 7, 11)):
                    duplicado, info = db.registrar_link_e_checar("https://instagram.com/reel/abc", 10, "Bia", 2)
                    self.assertFalse(duplicado)
                    self.assertEqual(info, {})

                with patch.object(db, "datetime", fixed_datetime(2026, 7, 11)):
                    duplicado, info = db.registrar_link_e_checar("https://instagram.com/reel/abc", 10, "Caio", 3)
                    self.assertTrue(duplicado)
                    self.assertEqual(info["primeiro_user"], "Bia")
                    self.assertEqual(info["vezes"], 2)

                with patch.object(db, "datetime", fixed_datetime(2026, 7, 11)):
                    ranking = db.get_lider_mes_atual()

                self.assertCountEqual(ranking, [("Bia", 1), ("Caio", 1)])

    def test_boca_de_leite_nao_dispara_quando_mesma_pessoa_repete_no_mesmo_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", str(Path(tmp) / "links.db")):
                db.init_db()

                duplicado, _ = db.registrar_link_e_checar("https://instagram.com/reel/self", 10, "Ana", 1)
                self.assertFalse(duplicado)

                duplicado, info = db.registrar_link_e_checar("https://instagram.com/reel/self", 10, "Ana", 1)
                self.assertFalse(duplicado)
                self.assertEqual(info, {})

                duplicado, info = db.registrar_link_e_checar("https://instagram.com/reel/self", 10, "Bia", 2)
                self.assertTrue(duplicado)
                self.assertEqual(info["primeiro_user"], "Ana")
                self.assertEqual(info["vezes"], 3)

                ranking = db.get_lider_mes_atual()
                self.assertEqual(ranking, [("Bia", 1)])


if __name__ == "__main__":
    unittest.main()
