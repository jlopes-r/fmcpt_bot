import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.ephemeral_commands import service as eph
from apps.ephemeral_commands.service import BotRuntime, EphemeralCommandService, bot_command_entities, parse_command
from packages.command_catalog import CommandSpec


async def async_true(*args, **kwargs):
    return True


async def async_none(*args, **kwargs):
    return None


class EphemeralCommandServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_command_strips_bot_username_and_args(self):
        self.assertEqual(parse_command("/menu@fmcpt_bot agora"), ("menu", ["agora"]))
        self.assertEqual(parse_command("menu"), ("", []))

    def test_custom_command_list_keeps_commands_clickable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "comandos_personalizados.json").write_text(
                '{"sorry": {"tipo": "texto"}, "teste": {"tipo": "gif"}}',
                encoding="utf-8",
            )
            with patch.object(eph, "CUSTOM_COMMANDS_FILE", data_dir / "comandos_personalizados.json"):
                text = eph.build_custom_command_list()

        self.assertIn("<b>📋 Comandos Personalizados:</b>", text)
        self.assertIn("/sorry", text)
        self.assertIn("/teste", text)
        self.assertNotIn("<code>/sorry</code>", text)

    def test_bot_command_entities_marks_slash_commands(self):
        text = "Use /cancelar para desistir e /teste no grupo."
        entities = bot_command_entities(text)

        self.assertEqual([item["type"] for item in entities], ["bot_command", "bot_command"])
        self.assertEqual(text[entities[0]["offset"]:entities[0]["offset"] + entities[0]["length"]], "/cancelar")
        self.assertEqual(text[entities[1]["offset"]:entities[1]["offset"] + entities[1]["length"]], "/teste")

    async def test_handle_menu_sends_ephemeral_message_to_sender(self):
        service = EphemeralCommandService()
        service.allowed_chats = {-100123}
        seen = []

        async def fake_send(session, api, chat_id, user_id, text, parse_mode=None):
            seen.append((chat_id, user_id, text, parse_mode))

        service.send_ephemeral = fake_send
        runtime = BotRuntime(
            "super",
            "123:abc",
            (CommandSpec("menu", "Mostra o menu", "Interface", ephemeral=True),),
            "Menu",
        )
        message = {
            "chat": {"id": -100123, "type": "supergroup"},
            "from": {"id": 456},
            "text": "/menu",
        }

        await service.handle_command(None, runtime, None, message)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], -100123)
        self.assertEqual(seen[0][1], 456)
        self.assertIn("<b>Menu</b>", seen[0][2])
        self.assertEqual(seen[0][3], "HTML")

    async def test_ephemeral_create_text_command_flow_persists_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            commands_file = data_dir / "comandos_personalizados.json"
            categories_file = data_dir / "categorias_comandos_personalizados.json"
            service = EphemeralCommandService()
            service.allowed_chats = {-100123}
            seen = []

            async def fake_send(session, api, chat_id, user_id, text, parse_mode=None, entities=None):
                seen.append((text, entities))

            service.send_ephemeral = fake_send
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = eph.BotApi("123:abc")
            base = {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 456}}

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file), \
                 patch.object(eph, "CUSTOM_CATEGORIES_FILE", categories_file), \
                 patch.object(eph, "set_bot_commands_via_bot_api", new=async_true), \
                 patch.object(eph, "set_bot_commands_menu_button_via_bot_api", new=async_true):
                await service.handle_command(None, runtime, api, {**base, "text": "/create"})
                await service.handle_command(None, runtime, api, {**base, "text": "saudacao"})
                await service.handle_command(None, runtime, api, {**base, "text": "texto"})
                await service.handle_command(None, runtime, api, {**base, "text": "Bom dia"})
                await service.handle_command(None, runtime, api, {**base, "text": "Mensagem de bom dia"})

                saved = eph.load_custom_commands()

        self.assertIn("saudacao", saved)
        self.assertEqual(saved["saudacao"]["tipo"], "texto")
        self.assertEqual(saved["saudacao"]["conteudo"], "Bom dia")
        self.assertEqual(saved["saudacao"]["origem"], "ephemeral_group")
        self.assertTrue(any("Comando criado" in item[0] for item in seen))
        self.assertTrue(any(
            entity["type"] == "bot_command"
            for text, entities in seen
            for entity in (entities or [])
            if "/cancelar" in text or "/saudacao" in text
        ))

    async def test_ephemeral_create_media_command_stores_file_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            commands_file = data_dir / "comandos_personalizados.json"
            categories_file = data_dir / "categorias_comandos_personalizados.json"
            service = EphemeralCommandService()
            service.allowed_chats = {-100123}
            service.send_ephemeral = async_none
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = eph.BotApi("123:abc")
            base = {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 456}}

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file), \
                 patch.object(eph, "CUSTOM_CATEGORIES_FILE", categories_file), \
                 patch.object(eph, "set_bot_commands_via_bot_api", new=async_true), \
                 patch.object(eph, "set_bot_commands_menu_button_via_bot_api", new=async_true):
                await service.handle_command(None, runtime, api, {**base, "text": "/create"})
                await service.handle_command(None, runtime, api, {**base, "text": "gifzao"})
                await service.handle_command(
                    None,
                    runtime,
                    api,
                    {**base, "animation": {"file_id": "gif-file-id"}, "caption": "Legenda"},
                )
                await service.handle_command(None, runtime, api, {**base, "text": "GIF importante"})

                saved = eph.load_custom_commands()

        self.assertEqual(saved["gifzao"]["tipo"], "gif")
        self.assertEqual(saved["gifzao"]["media_id"], "gif-file-id")
        self.assertEqual(saved["gifzao"]["conteudo"], "Legenda")

    async def test_ephemeral_delete_custom_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos_personalizados.json"
            commands_file.write_text('{"sorry": {"tipo": "texto"}}', encoding="utf-8")
            service = EphemeralCommandService()
            seen = []

            async def fake_send(*args, **kwargs):
                seen.append(args[4])

            service.send_ephemeral = fake_send

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file), \
                 patch.object(eph, "set_bot_commands_via_bot_api", new=async_true), \
                 patch.object(eph, "set_bot_commands_menu_button_via_bot_api", new=async_true):
                await service.delete_custom_command(None, eph.BotApi("123:abc"), -100123, 456, ["sorry"])
                saved = eph.load_custom_commands()

        self.assertNotIn("sorry", saved)
        self.assertTrue(any("apagado" in item for item in seen))


if __name__ == "__main__":
    unittest.main()
