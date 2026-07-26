import json
import unittest
from pathlib import Path

from packages.command_catalog import COMANDOS_BOT_COMMANDS, SUPER_COMMANDS


class MiniAppCatalogTest(unittest.TestCase):
    def test_catalog_json_reflete_catalogo_central(self):
        mini_app_dir = Path(__file__).resolve().parents[1] / "apps" / "mini_app"
        catalog_path = mini_app_dir / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        by_id = {bot["id"]: bot for bot in catalog["bots"]}

        self.assertEqual(
            [command["name"] for command in by_id["super"]["commands"]],
            [command.name for command in SUPER_COMMANDS if command.mini_app],
        )
        self.assertEqual(
            [command["name"] for command in by_id["comandos"]["commands"]],
            [command.name for command in COMANDOS_BOT_COMMANDS if command.mini_app],
        )

    def test_catalog_embutido_reflete_json(self):
        mini_app_dir = Path(__file__).resolve().parents[1] / "apps" / "mini_app"
        catalog = json.loads((mini_app_dir / "catalog.json").read_text(encoding="utf-8"))
        embedded = (mini_app_dir / "catalog-data.js").read_text(encoding="utf-8").strip()
        html = (mini_app_dir / "index.html").read_text(encoding="utf-8")
        prefix = "window.FMCPT_CATALOG = "

        self.assertTrue(embedded.startswith(prefix))
        self.assertTrue(embedded.endswith(";"))
        self.assertEqual(json.loads(embedded[len(prefix) : -1]), catalog)
        self.assertNotIn("catalog-data.js", html)


if __name__ == "__main__":
    unittest.main()
