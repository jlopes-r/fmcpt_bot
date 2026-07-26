import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.command_catalog import COMANDOS_BOT_COMMANDS, SUPER_COMMANDS
from packages.config import DATA_DIR


def command_to_frontend(command):
    return {
        "name": command.name,
        "category": command.category,
        "description": command.description,
        "aliases": list(command.aliases),
        "adminOnly": command.admin_only,
        "usage": command.usage,
    }


def load_json_file(path: Path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        pass
    return fallback


def build_catalog() -> dict:
    custom_commands = load_json_file(DATA_DIR / "comandos_personalizados.json", {})
    custom_categories = load_json_file(DATA_DIR / "categorias_comandos_personalizados.json", [])
    return {
        "bots": [
            {
                "id": "super",
                "name": "Super Bot",
                "description": "Downloads, rankings, castigos e utilidades do grupo.",
                "commands": [command_to_frontend(command) for command in SUPER_COMMANDS if command.mini_app],
            },
            {
                "id": "comandos",
                "name": "Comandos Bot",
                "description": "Comandos personalizados, GIFs e backlog.",
                "commands": [command_to_frontend(command) for command in COMANDOS_BOT_COMMANDS if command.mini_app],
                "customCommands": custom_commands,
                "customCategories": custom_categories,
            },
        ]
    }


def main() -> None:
    catalog = build_catalog()
    serialized = json.dumps(catalog, ensure_ascii=False, indent=2)
    mini_app_dir = ROOT / "apps" / "mini_app"

    (mini_app_dir / "catalog.json").write_text(
        serialized + "\n",
        encoding="utf-8",
    )
    (mini_app_dir / "catalog-data.js").write_text(
        f"window.FMCPT_CATALOG = {serialized};\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
