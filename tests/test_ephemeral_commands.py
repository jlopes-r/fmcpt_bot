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


class FakeApi:
    token = "123:abc"

    def __init__(self):
        self.posts = []

    async def post(self, session, method, payload):
        self.posts.append((method, payload))
        return {"ok": True, "result": True}


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

        self.assertIn("<b>📋 Comandos personalizados</b>", text)
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

            async def fake_send(session, api, chat_id, user_id, text, parse_mode=None, entities=None, reply_markup=None, callback_query_id=None):
                seen.append((text, entities, reply_markup))

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
            for text, entities, _ in seen
            for entity in (entities or [])
            if "/cancelar" in text or "/saudacao" in text
        ))
        self.assertTrue(any(
            markup and markup["inline_keyboard"][0][0]["callback_data"] == "create:cancel"
            for _, _, markup in seen
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
                seen.append((args[4], kwargs.get("reply_markup")))

            service.send_ephemeral = fake_send

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file), \
                 patch.object(eph, "set_bot_commands_via_bot_api", new=async_true), \
                 patch.object(eph, "set_bot_commands_menu_button_via_bot_api", new=async_true):
                await service.delete_custom_command(None, eph.BotApi("123:abc"), -100123, 456, ["sorry"])
                saved = eph.load_custom_commands()

        self.assertIn("sorry", saved)
        self.assertEqual(service.pending_deletes[("comandos", -100123, 456)], "sorry")
        self.assertTrue(any(
            markup and markup["inline_keyboard"][0][0]["callback_data"] == "delete:confirm:sorry"
            for _, markup in seen
        ))

    async def test_delete_confirm_callback_deletes_custom_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos_personalizados.json"
            commands_file.write_text('{"sorry": {"tipo": "texto"}}', encoding="utf-8")
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            service.pending_deletes[("comandos", -100123, 456)] = "sorry"
            callback = {
                "id": "callback-delete",
                "data": "delete:confirm:sorry",
                "from": {"id": 456},
                "message": {"chat": {"id": -100123, "type": "supergroup"}},
            }

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file), \
                 patch.object(eph, "set_bot_commands_via_bot_api", new=async_true), \
                 patch.object(eph, "set_bot_commands_menu_button_via_bot_api", new=async_true):
                await service.handle_callback_query(None, runtime, api, callback)
                saved = eph.load_custom_commands()

        self.assertNotIn("sorry", saved)
        self.assertNotIn(("comandos", -100123, 456), service.pending_deletes)

    def test_custom_command_list_view_filters_and_paginates(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos_personalizados.json"
            commands = {
                **{f"txt{i}": {"tipo": "texto"} for i in range(40)},
                "gifzao": {"tipo": "gif"},
            }
            commands_file.write_text(eph.json.dumps(commands), encoding="utf-8")

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file):
                text, markup = eph.build_custom_command_list_view("texto", 1)

        self.assertIn("Página 2/2", text)
        self.assertIn("/txt", text)
        callback_data = [
            button["callback_data"]
            for row in markup["inline_keyboard"]
            for button in row
        ]
        self.assertIn("list:texto:0", callback_data)
        self.assertIn("list:texto:0", callback_data)

    async def test_list_callback_sends_filtered_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos_personalizados.json"
            commands_file.write_text('{"foto1": {"tipo": "foto"}}', encoding="utf-8")
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            callback = {
                "id": "callback-list",
                "data": "list:foto:0",
                "from": {"id": 456},
                "message": {"chat": {"id": -100123, "type": "supergroup"}, "ephemeral_message_id": 99},
            }

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file):
                await service.handle_callback_query(None, runtime, api, callback)

        edits = [payload for method, payload in api.posts if method == "editEphemeralMessageText"]
        self.assertIn("/foto1", edits[-1]["text"])
        self.assertEqual(edits[-1]["ephemeral_message_id"], 99)
        self.assertEqual(edits[-1]["receiver_user_id"], 456)

    async def test_list_callback_without_ephemeral_message_id_sends_fallback_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands_file = Path(tmp) / "comandos_personalizados.json"
            commands_file.write_text('{"foto1": {"tipo": "foto"}}', encoding="utf-8")
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            callback = {
                "id": "callback-list",
                "data": "list:foto:0",
                "from": {"id": 456},
                "message": {"chat": {"id": -100123, "type": "supergroup"}},
            }

            with patch.object(eph, "CUSTOM_COMMANDS_FILE", commands_file):
                await service.handle_callback_query(None, runtime, api, callback)

        send_messages = [payload for method, payload in api.posts if method == "sendMessage"]
        self.assertIn("/foto1", send_messages[-1]["text"])
        self.assertEqual(send_messages[-1]["callback_query_id"], "callback-list")

    async def test_create_type_text_callback_advances_state_ephemerally(self):
        service = EphemeralCommandService()
        runtime = BotRuntime("comandos", "123:abc", (), "Menu")
        api = FakeApi()
        service.create_states[("comandos", -100123, 456)] = {
            "step": "type",
            "data": {"name": "saudacao"},
            "started_at": 1,
        }
        callback = {
            "id": "callback-1",
            "data": "create:type:text",
            "from": {"id": 456},
            "message": {"chat": {"id": -100123, "type": "supergroup"}},
        }

        await service.handle_callback_query(None, runtime, api, callback)

        self.assertEqual(service.create_states[("comandos", -100123, 456)]["step"], "text_content")
        self.assertIn(("answerCallbackQuery", {"callback_query_id": "callback-1"}), api.posts)
        send_messages = [payload for method, payload in api.posts if method == "sendMessage"]
        self.assertEqual(send_messages[-1]["callback_query_id"], "callback-1")
        self.assertEqual(send_messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "create:cancel")

    async def test_create_cancel_callback_clears_state(self):
        service = EphemeralCommandService()
        runtime = BotRuntime("comandos", "123:abc", (), "Menu")
        api = FakeApi()
        service.create_states[("comandos", -100123, 456)] = {
            "step": "name",
            "data": {},
            "started_at": 1,
        }
        callback = {
            "id": "callback-2",
            "data": "create:cancel",
            "from": {"id": 456},
            "message": {"chat": {"id": -100123, "type": "supergroup"}},
        }

        await service.handle_callback_query(None, runtime, api, callback)

        self.assertNotIn(("comandos", -100123, 456), service.create_states)
        send_messages = [payload for method, payload in api.posts if method == "sendMessage"]
        self.assertIn("cancelada", send_messages[-1]["text"])
        self.assertEqual(send_messages[-1]["callback_query_id"], "callback-2")

    async def test_backlog_add_callback_and_state_persist_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog_file = Path(tmp) / "backlog.json"
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            callback = {
                "id": "callback-backlog-add",
                "data": "backlog:add",
                "from": {"id": 456},
                "message": {"chat": {"id": -100123, "type": "supergroup"}},
            }
            message = {
                "chat": {"id": -100123, "type": "supergroup"},
                "from": {"id": 456, "first_name": "Ana"},
                "text": "Nova sugestão",
            }

            with patch.object(eph, "BACKLOG_FILE", backlog_file):
                await service.handle_callback_query(None, runtime, api, callback)
                await service.handle_backlog_state(None, runtime, api, message)
                backlog = eph.load_backlog()

        self.assertEqual(backlog[0]["sugestao"], "Nova sugestão")
        self.assertNotIn(("comandos", -100123, 456), service.backlog_states)

    async def test_backlog_view_callback_edits_ephemeral_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog_file = Path(tmp) / "backlog.json"
            backlog_file.write_text('[{"id": 1, "sugestao": "Resolver bug"}]', encoding="utf-8")
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            callback = {
                "id": "callback-backlog-view",
                "data": "backlog:view:0",
                "from": {"id": 456},
                "message": {"chat": {"id": -100123, "type": "supergroup"}, "ephemeral_message_id": 77},
            }

            with patch.object(eph, "BACKLOG_FILE", backlog_file):
                await service.handle_callback_query(None, runtime, api, callback)

        edits = [payload for method, payload in api.posts if method == "editEphemeralMessageText"]
        self.assertIn("Resolver bug", edits[-1]["text"])
        self.assertEqual(edits[-1]["ephemeral_message_id"], 77)
        self.assertEqual(edits[-1]["receiver_user_id"], 456)

    async def test_backlog_done_state_removes_matched_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog_file = Path(tmp) / "backlog.json"
            backlog_file.write_text('[{"id": 7, "sugestao": "Resolver bug"}]', encoding="utf-8")
            service = EphemeralCommandService()
            runtime = BotRuntime("comandos", "123:abc", (), "Menu")
            api = FakeApi()
            service.backlog_states[("comandos", -100123, 456)] = {"action": "done"}
            message = {
                "chat": {"id": -100123, "type": "supergroup"},
                "from": {"id": 456},
                "text": "7",
            }

            with patch.object(eph, "BACKLOG_FILE", backlog_file):
                await service.handle_backlog_state(None, runtime, api, message)
                backlog = eph.load_backlog()

        self.assertEqual(backlog, [])


if __name__ == "__main__":
    unittest.main()
