import json
import tempfile
import unittest
from pathlib import Path

from packages.json_store import load_json, save_json, update_json


class JsonStoreTest(unittest.TestCase):
    def test_save_json_replaces_document_and_loads_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"

            save_json(path, {"commands": ["ola"]})

            self.assertEqual(load_json(path, {}), {"commands": ["ola"]})
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_load_json_returns_independent_default_for_invalid_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not-json", encoding="utf-8")

            loaded = load_json(path, [])
            loaded.append("local")

            self.assertEqual(load_json(path, []), [])

    def test_update_json_reads_and_writes_under_one_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_json(path, {"count": 1})

            updated = update_json(path, {}, lambda state: {"count": state["count"] + 1})

            self.assertEqual(updated, {"count": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"count": 2})


if __name__ == "__main__":
    unittest.main()
