import tempfile
import unittest
from pathlib import Path

from packages.json_store import load_json, merge_mapping_changes, save_json, update_json


class ComandosPersistenceTest(unittest.TestCase):
    def test_local_save_preserves_command_added_by_another_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos.json"
            initial = {"ola": {"tipo": "texto", "conteudo": "antigo"}}
            local = {"ola": {"tipo": "texto", "conteudo": "novo"}}
            save_json(commands_file, initial)
            save_json(
                commands_file,
                {
                    **initial,
                    "remoto": {"tipo": "texto", "conteudo": "criado no painel"},
                },
            )

            update_json(
                commands_file,
                {},
                lambda current: merge_mapping_changes(current, initial, local),
            )
            saved = load_json(commands_file, {})

        self.assertEqual(saved["ola"]["conteudo"], "novo")
        self.assertEqual(saved["remoto"]["conteudo"], "criado no painel")


if __name__ == "__main__":
    unittest.main()
