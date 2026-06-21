import os
import sys
import re
import json
import logging
from pathlib import Path
from datetime import datetime

from functools import wraps
from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio
from dotenv import load_dotenv

# Configuração
def flex_command(commands, prefixes="/", case_sensitive=False):
    if isinstance(commands, str):
        commands = [commands]
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    
    commands = [c if case_sensitive else c.lower() for c in commands]
    
    async def func(flt, client, message):
        text = message.text or message.caption
        message.command = None
        if not text:
            return False
            
        words = text.split()
        for i, word in enumerate(words):
            for prefix in prefixes:
                if word.startswith(prefix):
                    cmd_name = word[len(prefix):].split('@')[0]
                    if not case_sensitive:
                        cmd_name = cmd_name.lower()
                    if cmd_name in flt.commands:
                        message.command = [cmd_name] + words[i+1:]
                        return True
        return False
        
    return filters.create(func, commands=commands)

filters.command = flex_command

# O caminho para o arquivo .env é definido de forma absoluta para garantir
# que o bot funcione corretamente quando executado como um serviço na VM.
CAMINHO_ENV = "/home/juanl/fmcpt_bot/apps/telegram_bot/.env"
load_dotenv(CAMINHO_ENV)
load_dotenv(CAMINHO_ENV)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN_COMANDOS")
GRUPOS_AUTORIZADOS_STR = os.getenv("GRUPOS_AUTORIZADOS", "")
GRUPOS_AUTORIZADOS = [int(chat_id.strip()) for chat_id in GRUPOS_AUTORIZADOS_STR.split(',') if chat_id.strip()]

# Logging
log = logging.getLogger("ComandosBot")
logging.basicConfig(level=logging.INFO)

# Caminho para arquivo de comandos personalizados, usando caminho absoluto
# para garantir que seja encontrado quando executado como serviço.
CAMINHO_RAIZ_PROJETO = "/home/juanl/fmcpt_bot"
COMANDOS_FILE = Path(CAMINHO_RAIZ_PROJETO) / "data" / "comandos_personalizados.json"
BACKLOG_FILE = Path(CAMINHO_RAIZ_PROJETO) / "data" / "backlog.json"
MERDA_FILE = Path(CAMINHO_RAIZ_PROJETO) / "data" / "sugestoes_de_merda.json"

# Caminhos para as bases de dados de GIFs
GIFS_CATOLICOS_FILE = Path(CAMINHO_RAIZ_PROJETO) / "data" / "gifs_catolicos.json"
GIFS_DUVIDA_FILE = Path(CAMINHO_RAIZ_PROJETO) / "data" / "gifs_interrogacao.json"

# Estado da conversa para criar comandos
user_states = {}

# Lista de comandos internos que o bot reconhece nativamente
COMANDOS_INTERNOS = ["start", "help", "menu", "id", "create", "list", "delete", "instance", "duvida", "add", "removegif", "gifstats", "sync", "cancelar", "backlog", "done", "merda", "clearbacklog", "backlogmerda"]

# Decorator para verificar se o usuário está autorizado
def admin_only(func):
    @wraps(func)
    async def wrapped(client, message, *args, **kwargs):
        chat_id = message.chat.id
        if not GRUPOS_AUTORIZADOS or chat_id not in GRUPOS_AUTORIZADOS:
            error_msg = (
                f"🚫 **Acesso Negado** 🚫\n\n"
                f"Seu ID de conversa (`{chat_id}`) não tem permissão para usar este comando.\n\n"
                f"Por favor, entre em contato com o administrador do bot."
            )
            await message.reply_text(error_msg)
            return
        return await func(client, message, *args, **kwargs)
    return wrapped


# Carrega comandos salvos
def carregar_comandos():
    if COMANDOS_FILE.exists():
        with open(COMANDOS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def salvar_comandos(comandos):
    COMANDOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COMANDOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(comandos, f, ensure_ascii=False, indent=2)

# Carrega e salva backlog de sugestões
def carregar_backlog():
    if BACKLOG_FILE.exists():
        try:
            with open(BACKLOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            log.error(f"Erro ao carregar backlog: {e}")
    return []

def salvar_backlog(backlog):
    BACKLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKLOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(backlog, f, ensure_ascii=False, indent=2)

def dividir_texto_longo(texto: str, limite: int = 4096) -> list[str]:
    """Divide texto longo em múltiplas mensagens respeitando o limite do Telegram."""
    if len(texto) <= limite:
        return [texto]
    partes = []
    while texto:
        if len(texto) <= limite:
            partes.append(texto)
            break
        corte = texto.rfind('\n', 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = texto.rfind(' ', 0, limite)
        if corte == -1 or corte < limite // 2:
            corte = limite
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip()
    return partes

def carregar_merda():
    if MERDA_FILE.exists():
        try:
            with open(MERDA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            log.error(f"Erro ao carregar sugestões de merda: {e}")
    return []

def salvar_merda(merda):
    MERDA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MERDA_FILE, 'w', encoding='utf-8') as f:
        json.dump(merda, f, ensure_ascii=False, indent=2)

def carregar_gifs(filepath):
    """Carrega uma lista de GIFs de um arquivo JSON."""
    log.info(f"Tentando carregar GIFs do arquivo: {filepath}")
    try:
        if filepath.exists():
            log.info(f"Arquivo encontrado. Lendo o conteúdo...")
            with open(filepath, 'r', encoding='utf-8') as f:
                gifs = json.load(f)
                log.info(f"Sucesso! {len(gifs)} GIFs carregados de {filepath}.")
                return gifs
        else:
            log.warning(f"O arquivo de GIFs não foi encontrado em: {filepath}")
    except json.JSONDecodeError as e:
        log.error(f"Erro de sintaxe no JSON em {filepath}: {e}")
    except Exception as e:
        log.error(f"Erro inesperado ao carregar GIFs de {filepath}: {e}")
    return []

def salvar_gifs(filepath, gifs_list):
    """Salva a lista de GIFs em um arquivo JSON."""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(gifs_list, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        log.error(f"Erro ao salvar GIFs em {filepath}: {e}")
        return False

# Carrega as bases de dados de GIFs
gifs_catolicos = carregar_gifs(GIFS_CATOLICOS_FILE)
gifs_duvida = carregar_gifs(GIFS_DUVIDA_FILE)
log.info(f"GIFs carregados: {len(gifs_catolicos)} católicos, {len(gifs_duvida)} dúvida")

async def atualizar_menu_comandos(client):
    try:
        from pyrogram.types import BotCommand
        lista_comandos = [
            BotCommand("start", "Inicia o bot"),
            BotCommand("menu", "Abre o menu principal"),
            BotCommand("list", "Lista comandos personalizados"),
            BotCommand("create", "Cria um comando"),
            BotCommand("delete", "Deleta um comando"),
            BotCommand("instance", "Envia um GIF de bom dia abençoado"),
            BotCommand("duvida", "Envia um GIF de dúvida/interrogação"),
            BotCommand("add", "➕ Adiciona um GIF (responda a um GIF)"),
            BotCommand("removegif", "🗑️ Remove um GIF da base"),
            BotCommand("gifstats", "📊 Estatísticas dos GIFs"),
            BotCommand("backlog", "💡 Sugestões para o bot"),
            BotCommand("done", "✅ Marca sugestão como feita"),
            BotCommand("merda", "💩 Move sugestão pro lixo"),
            BotCommand("backlogmerda", "💩 Lista as sugestões no lixo"),
            BotCommand("clearbacklog", "🧹 Limpa todo o backlog"),
            BotCommand("sync", "🔄 Sincroniza o menu de comandos")
        ]
        
        adicionados = 0
        tipo_emoji = {'texto': '📝', 'foto': '🖼️', 'video': '🎬', 'audio': '🎵', 'voice': '🎤', 'gif': '🎞️'}
        for cmd, info in comandos_personalizados.items():
            # Limite do Telegram: 100 comandos no total
            if len(lista_comandos) + adicionados >= 100:
                log.warning("Limite de 100 comandos do Telegram atingido. Alguns comandos personalizados não serão exibidos.")
                break
            if adicionados >= 87:  # 13 internos + 87 personalizados = 100
                break
            
            cmd_formatado = cmd.lower()
            # Telegram apenas aceita [a-z0-9_] e até 32 caracteres. Pula se for inválido.
            if not re.match(r'^[a-z0-9_]{1,32}$', cmd_formatado):
                continue
                
            tipo = info.get('tipo', 'texto')
            emoji = tipo_emoji.get(tipo, '❓')
            desc = f"{emoji} {info.get('descricao', 'Sem descrição')}"
            
            if len(desc) > 60:
                desc = desc[:57] + "..."
            lista_comandos.append(BotCommand(cmd_formatado, desc))
            adicionados += 1
            
        await client.set_bot_commands(lista_comandos)
        log.info(f"Menu de comandos atualizado no Telegram! ({len(lista_comandos)} comandos)")
        return True
    except Exception as e:
        log.error(f"Erro ao atualizar menu: {e}", exc_info=True)
        return False

# Cliente
app = Client(
    "meu_comandos_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Comandos personalizados carregados
comandos_personalizados = carregar_comandos()

async def executar_comando_personalizado(client, message, nome, info):
    """Executa um comando personalizado"""
    chat_id = message.chat.id
    if not GRUPOS_AUTORIZADOS or chat_id not in GRUPOS_AUTORIZADOS:
        error_msg = (
            f"🚫 **Acesso Negado** 🚫\n\n"
            f"Seu ID de conversa (`{chat_id}`) não tem permissão para usar comandos personalizados.\n\n"
            f"Por favor, entre em contato com o administrador do bot."
        )
        await message.reply_text(error_msg)
        return
        
    try:
        tipo = info.get('tipo', 'texto')
        conteudo = info.get('conteudo', '')
        
        reply_to = message.reply_to_message.id if message.reply_to_message else message.id
        
        if tipo == 'texto':
            for parte in dividir_texto_longo(conteudo):
                await client.send_message(message.chat.id, parte, reply_to_message_id=reply_to)
        elif tipo == 'foto' and 'media_id' in info:
            await client.send_photo(message.chat.id, info['media_id'], caption=conteudo, reply_to_message_id=reply_to)
        elif tipo == 'video' and 'media_id' in info:
            await client.send_video(message.chat.id, info['media_id'], caption=conteudo, reply_to_message_id=reply_to)
        elif tipo == 'audio' and 'media_id' in info:
            await client.send_audio(message.chat.id, info['media_id'], caption=conteudo, reply_to_message_id=reply_to)
        elif tipo == 'voice' and 'media_id' in info:
            await client.send_voice(message.chat.id, info['media_id'], caption=conteudo, reply_to_message_id=reply_to)
        elif tipo == 'gif' and 'media_id' in info:
            await client.send_animation(message.chat.id, info['media_id'], caption=conteudo, reply_to_message_id=reply_to)
        else:
            await message.reply_text(f"❌ Erro: tipo de comando não suportado")
    except Exception as e:
        log.error(f"Erro ao executar comando {nome}: {e}")
        await message.reply_text("❌ Erro ao executar comando")

@app.on_message(filters.command("start"))
async def cmd_start(client, message):
    chat_id = message.chat.id
    if not GRUPOS_AUTORIZADOS or chat_id not in GRUPOS_AUTORIZADOS:
        error_msg = (
            f"🚫 **Acesso Negado** 🚫\n\n"
            f"Seu ID de conversa (`{chat_id}`) não tem permissão para usar este comando.\n\n"
            f"Por favor, entre em contato com o administrador do bot."
        )
        await message.reply_text(error_msg)
        return
    await message.reply_text("🤖 Olá! Sou seu Bot de Comandos. Use /menu para ver o que posso fazer.")

@app.on_message(filters.command(["help", "menu"]))
async def cmd_menu(client, message):
    txt = (
        "**🤖 MENU DE COMANDOS 🤖**\n\n"
        "Aqui está tudo que eu posso fazer:\n\n"
        "**🛠️ Comandos de Administração:**\n"
        "▫️ `/start` - Inicia a nossa conversa\n"
        "▫️ `/menu` ou `/help` - Exibe este menu\n"
        "▫️ `/id` - Mostra o ID desta conversa\n"
        "▫️ `/create` - 🆕 Cria um novo comando personalizado\n"
        "▫️ `/list` - 📋 Lista todos os seus comandos\n"
        "▫️ `/delete NOME` - 🗑️ Deleta um comando\n"
        "▫️ `/sync` - 🔄 Sincroniza o menu do Telegram\n\n"
        "**🎞️ GIFs:**\n"
        "▫️ `/instance` - 🙏 Envia um GIF de bom dia abençoado\n"
        "▫️ `/duvida` - ❓ Envia um GIF de dúvida/interrogação\n"
        "▫️ `/add instance` ou `/add duvida` - ➕ Adiciona um GIF (responda a um GIF)\n"
        "▫️ `/removegif` - 🗑️ Remove um GIF (responda a um GIF do bot)\n"
        "▫️ `/gifstats` - 📊 Mostra quantos GIFs tem em cada base\n\n"
        "**💡 Backlog:**\n"
        "▫️ `/backlog` - 📋 Lista todas as sugestões pendentes\n"
        "▫️ `/backlog SUGESTÃO` - ➕ Adiciona uma nova sugestão\n"
        "▫️ `/done TEXTO` - ✅ Marca uma sugestão como concluída\n"
        "▫️ `/merda TEXTO` - 💩 Move uma sugestão pro lixo\n"
        "▫️ `/backlogmerda` - 💩 Ver a lista da lixeira\n\n"
    )
    
    if comandos_personalizados:
        txt += "**✨ Comandos Personalizados:**\n"
        txt += "👉 Use `/list` para ver a lista com todos os seus comandos criados!"
    
    await message.reply_text(txt)

import random

@app.on_message(filters.command("instance"))
async def cmd_instance(client, message):
    chat_id = message.chat.id
    if GRUPOS_AUTORIZADOS and chat_id not in GRUPOS_AUTORIZADOS:
        return
    
    if not gifs_catolicos:
        await message.reply_text("❌ Nenhum GIF católico encontrado na base de dados.")
        return
        
    gif_escolhido = random.choice(gifs_catolicos)
    try:
        reply_to = message.reply_to_message.id if message.reply_to_message else message.id
        await client.send_animation(message.chat.id, gif_escolhido, reply_to_message_id=reply_to)
    except Exception as e:
        log.error(f"Erro ao enviar gif /instance: {e}")

@app.on_message(filters.command("duvida"))
async def cmd_duvida(client, message):
    """Envia um GIF aleatório de dúvida/interrogação."""
    chat_id = message.chat.id
    if GRUPOS_AUTORIZADOS and chat_id not in GRUPOS_AUTORIZADOS:
        return
    
    if not gifs_duvida:
        await message.reply_text("❌ Nenhum GIF de dúvida encontrado na base de dados.")
        return
        
    gif_escolhido = random.choice(gifs_duvida)
    try:
        reply_to = message.reply_to_message.id if message.reply_to_message else message.id
        await client.send_animation(message.chat.id, gif_escolhido, reply_to_message_id=reply_to)
    except Exception as e:
        log.error(f"Erro ao enviar gif /duvida: {e}")

@app.on_message(filters.command("add"))
@admin_only
async def cmd_add_gif(client, message):
    """Adiciona um GIF à base de /instance ou /duvida. Uso: responder a um GIF com /add instance ou /add duvida."""
    global gifs_catolicos, gifs_duvida
    
    # Verifica se o comando tem argumento
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Uso:** Responda a um GIF com:\n\n"
            "▫️ `/add instance` - para adicionar à base de bom dia abençoado\n"
            "▫️ `/add duvida` - para adicionar à base de dúvida/interrogação"
        )
        return
    
    categoria = message.command[1].lower()
    if categoria not in ("instance", "duvida"):
        await message.reply_text(
            "❌ Categoria inválida! Use:\n\n"
            "▫️ `/add instance` - GIFs de bom dia abençoado\n"
            "▫️ `/add duvida` - GIFs de dúvida/interrogação"
        )
        return
    
    # Verifica se está respondendo a uma mensagem
    if not message.reply_to_message:
        await message.reply_text("❌ Você precisa **responder a um GIF** com este comando!")
        return
    
    # Verifica se a mensagem respondida contém uma animation (GIF)
    replied = message.reply_to_message
    if not replied.animation:
        await message.reply_text("❌ A mensagem respondida não é um **GIF**! Encaminhe ou envie um GIF e responda com `/add`.")
        return
    
    gif_file_id = replied.animation.file_id
    
    # Determina a base e o arquivo correspondente
    if categoria == "instance":
        gif_list = gifs_catolicos
        gif_file = GIFS_CATOLICOS_FILE
        nome_base = "bom dia abençoado (instance)"
    else:
        gif_list = gifs_duvida
        gif_file = GIFS_DUVIDA_FILE
        nome_base = "dúvida/interrogação (duvida)"
    
    # Verifica duplicata
    if gif_file_id in gif_list:
        await message.reply_text("⚠️ Esse GIF já existe na base!")
        return
    
    # Adiciona e salva
    gif_list.append(gif_file_id)
    if salvar_gifs(gif_file, gif_list):
        # Atualiza a referência global
        if categoria == "instance":
            gifs_catolicos = gif_list
        else:
            gifs_duvida = gif_list
        
        usuario = message.from_user.first_name or "Alguém"
        await message.reply_text(
            f"✅ **GIF adicionado com sucesso!**\n\n"
            f"📂 Base: {nome_base}\n"
            f"📊 Total de GIFs: **{len(gif_list)}**\n"
            f"👤 Adicionado por: {usuario}"
        )
        log.info(f"GIF adicionado à base '{categoria}' por {usuario}. Total: {len(gif_list)}")
    else:
        await message.reply_text("❌ Erro ao salvar o GIF. Tente novamente.")

@app.on_message(filters.command("removegif"))
@admin_only
async def cmd_remove_gif(client, message):
    """Remove um GIF de qualquer base. Uso: responder a um GIF com /removegif."""
    global gifs_catolicos, gifs_duvida
    
    if not message.reply_to_message:
        await message.reply_text("❌ Você precisa **responder a um GIF** com `/removegif` para removê-lo.")
        return
    
    replied = message.reply_to_message
    if not replied.animation:
        await message.reply_text("❌ A mensagem respondida não é um **GIF**!")
        return
    
    gif_file_id = replied.animation.file_id
    removido = False
    base_nome = ""
    
    # Procura nas duas bases
    if gif_file_id in gifs_catolicos:
        gifs_catolicos.remove(gif_file_id)
        salvar_gifs(GIFS_CATOLICOS_FILE, gifs_catolicos)
        removido = True
        base_nome = "bom dia abençoado (instance)"
    
    if gif_file_id in gifs_duvida:
        gifs_duvida.remove(gif_file_id)
        salvar_gifs(GIFS_DUVIDA_FILE, gifs_duvida)
        removido = True
        base_nome = "dúvida/interrogação (duvida)" if not base_nome else base_nome + " e dúvida/interrogação (duvida)"
    
    if removido:
        usuario = message.from_user.first_name or "Alguém"
        await message.reply_text(
            f"🗑️ **GIF removido com sucesso!**\n\n"
            f"📂 Base: {base_nome}\n"
            f"👤 Removido por: {usuario}"
        )
        log.info(f"GIF removido da base '{base_nome}' por {usuario}")
    else:
        await message.reply_text(
            "❌ Esse GIF **não foi encontrado** em nenhuma base.\n\n"
            "💡 Dica: O `/removegif` só funciona com GIFs que foram adicionados via `/add`. "
            "GIFs antigos (por URL) precisam ser removidos manualmente."
        )

@app.on_message(filters.command("gifstats"))
@admin_only
async def cmd_gifstats(client, message):
    """Mostra estatísticas das bases de GIFs."""
    urls_catolicos = sum(1 for g in gifs_catolicos if g.startswith('http'))
    ids_catolicos = len(gifs_catolicos) - urls_catolicos
    urls_duvida = sum(1 for g in gifs_duvida if g.startswith('http'))
    ids_duvida = len(gifs_duvida) - urls_duvida
    
    txt = (
        "**📊 Estatísticas dos GIFs**\n\n"
        f"🙏 **Instance** (Bom dia abençoado):\n"
        f"   Total: **{len(gifs_catolicos)}** GIFs\n"
        f"   ├ URLs: {urls_catolicos}\n"
        f"   └ Telegram: {ids_catolicos}\n\n"
        f"❓ **Dúvida** (Interrogação):\n"
        f"   Total: **{len(gifs_duvida)}** GIFs\n"
        f"   ├ URLs: {urls_duvida}\n"
        f"   └ Telegram: {ids_duvida}"
    )
    await message.reply_text(txt)

@app.on_message(filters.command("id"))
async def cmd_id(client, message):
    await message.reply_text(f"O ID desta conversa é: `{message.chat.id}`")

# Filtro para verificar se o usuário está no processo de criação de um comando
async def filtro_estado_usuario(_, __, message):
    return message.from_user.id in user_states

@app.on_message(filters.command("create"))
@admin_only
async def cmd_create(client, message):
    user_id = message.from_user.id
    user_states[user_id] = {
        'etapa': 'nome',
        'dados': {}
    }
    await message.reply_text(
        "📝 **Criação de Comando Personalizado**\n\n"
        "Digite o nome do comando (sem a barra, ex: `frias`):\n\n"
        "*(Digite /cancelar a qualquer momento para desistir)*"
    )


@app.on_message(filters.command("cancelar") & filters.create(filtro_estado_usuario))
@admin_only
async def cmd_cancelar(client, message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
        await message.reply_text("❌ Criação/edição de comando cancelada.")

@app.on_message(filters.command("list"))
@admin_only
async def cmd_list(client, message):
    if not comandos_personalizados:
        await message.reply_text("📭 Nenhum comando personalizado criado.")
        return
    
    txt = "**📋 Comandos Personalizados:**\n\n"
    tipo_emoji = {'texto': '📝', 'foto': '🖼️', 'video': '🎬', 'audio': '🎵', 'voice': '🎤', 'gif': '🎞️'}
    for cmd, info in comandos_personalizados.items():
        emoji = tipo_emoji.get(info.get('tipo', 'texto'), '❓')
        txt += f"▫️ `/{cmd}` {emoji} - {info.get('descricao', 'Sem descrição')}\n"
    
    for parte in dividir_texto_longo(txt):
        await message.reply_text(parte)

@app.on_message(filters.command("delete"))
@admin_only
async def cmd_delete(client, message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Uso:** `/delete NOME_DO_COMANDO`")
        return
    
    cmd_nome = message.command[1].lstrip('/')
    
    if cmd_nome in comandos_personalizados:
        del comandos_personalizados[cmd_nome]
        salvar_comandos(comandos_personalizados)
        await atualizar_menu_comandos(client)
        await message.reply_text(f"✅ Comando `/{cmd_nome}` deletado com sucesso!")
    else:
        await message.reply_text(f"❌ Comando `/{cmd_nome}` não encontrado.")

@app.on_message(filters.command("sync"))
@admin_only
async def cmd_sync(client, message):
    sucesso = await atualizar_menu_comandos(client)
    if sucesso:
        await message.reply_text("✅ Menu do Telegram (botão /) atualizado com todos os comandos!")
    else:
        await message.reply_text("❌ Erro ao atualizar o menu. Veja os logs.")

# -----------------------------------------
# BACKLOG DE SUGESTÕES
# -----------------------------------------
backlog_sugestoes = carregar_backlog()

@app.on_message(filters.command("backlog"))
@admin_only
async def cmd_backlog(client, message):
    global backlog_sugestoes
    
    # Se tem argumento, adiciona nova sugestão
    texto_cmd = message.text or ""
    # Remove o /backlog (e possível @nomedobot) do início
    partes = texto_cmd.split(maxsplit=1)
    sugestao_texto = partes[1].strip() if len(partes) > 1 else ""
    
    if sugestao_texto:
        # Gera próximo ID
        proximo_id = max((s.get('id', 0) for s in backlog_sugestoes), default=0) + 1
        
        nova_sugestao = {
            'id': proximo_id,
            'sugestao': sugestao_texto,
            'autor': message.from_user.first_name or "Anônimo",
            'autor_id': message.from_user.id,
            'data': str(datetime.now().strftime('%d/%m/%Y %H:%M'))
        }
        backlog_sugestoes.append(nova_sugestao)
        salvar_backlog(backlog_sugestoes)
        
        await message.reply_text(
            f"✅ **Sugestão adicionada ao backlog!**\n\n"
            f"📝 #{proximo_id}: {sugestao_texto}\n"
            f"👤 Por: {nova_sugestao['autor']}\n"
            f"📊 Total no backlog: **{len(backlog_sugestoes)}** sugestões"
        )
        return
    
    # Sem argumento: lista o backlog
    if not backlog_sugestoes:
        await message.reply_text(
            "📭 **Backlog vazio!**\n\n"
            "Nenhuma sugestão pendente. Use:\n"
            "`/backlog sua sugestão aqui` para adicionar uma."
        )
        return
    
    txt = "**💡 BACKLOG DE SUGESTÕES**\n\n"
    for i, s in enumerate(backlog_sugestoes, 1):
        txt += (
            f"**{i}. #{s.get('id', '?')}** — {s['sugestao']}\n"
            f"   👤 {s.get('autor', '?')} • 📅 {s.get('data', '?')}\n\n"
        )
    txt += f"📊 **Total: {len(backlog_sugestoes)} sugestões pendentes**\n\n"
    txt += "💡 Use `/done id1, id2` ou textos para remover sugestões concluídas.\n"
    txt += "💩 Use `/merda id1, id2` para descartar sugestões ruins."

    for parte in dividir_texto_longo(txt):
        await message.reply_text(parte)

@app.on_message(filters.command("done"))
@admin_only
async def cmd_done(client, message):
    global backlog_sugestoes
    
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Uso:** `/done id_ou_texto, outro_id...`\n\n"
            "Você pode remover múltiplos itens separando por vírgula.\n"
            "Exemplo: `/done 5, 7, macarrão`."
        )
        return
    
    argumentos = " ".join(message.command[1:]).split(',')
    buscas = [arg.strip().lower() for arg in argumentos if arg.strip()]
    
    removidas = []
    nao_encontradas = []
    
    for busca in buscas:
        encontradas = []
        if busca.isdigit():
            id_busca = int(busca)
            encontradas = [s for s in backlog_sugestoes if s.get('id') == id_busca]
        else:
            encontradas = [s for s in backlog_sugestoes if busca in s['sugestao'].lower()]
            
        if not encontradas:
            nao_encontradas.append(f"\"{busca}\" (Não encontrada)")
        elif len(encontradas) > 1:
            nao_encontradas.append(f"\"{busca}\" (Múltiplas encontradas, seja mais específico)")
        else:
            if encontradas[0] not in removidas:
                removidas.append(encontradas[0])
    
    if not removidas:
        await message.reply_text(
            f"❌ Nenhuma sugestão pôde ser removida.\n" +
            ("\n".join(nao_encontradas) if nao_encontradas else "")
        )
        return
        
    ids_removidos = [s.get('id') for s in removidas]
    backlog_sugestoes = [s for s in backlog_sugestoes if s.get('id') not in ids_removidos]
    salvar_backlog(backlog_sugestoes)
    
    txt = f"✅ **{len(removidas)} Sugestão(ões) concluída(s)!**\n\n"
    for s in removidas:
        txt += f"✅ #{s.get('id', '?')}: {s['sugestao']}\n"
        
    if nao_encontradas:
        txt += "\n⚠️ **Não foi possível remover:**\n"
        for n in nao_encontradas:
            txt += f"▫️ {n}\n"
            
    txt += f"\n📊 Restam **{len(backlog_sugestoes)}** sugestões no backlog."
    await message.reply_text(txt)

sugestoes_merda = carregar_merda()

@app.on_message(filters.command("backlogmerda"))
@admin_only
async def cmd_backlogmerda(client, message):
    if not sugestoes_merda:
        await message.reply_text(
            "📭 **Lixeira vazia!**\n\n"
            "Nenhuma sugestão descartada até agora."
        )
        return
    
    txt = "**💩 SUGESTÕES DESCARTADAS (Lixeira)**\n\n"
    for i, s in enumerate(sugestoes_merda, 1):
        txt += (
            f"**{i}. #{s.get('id', '?')}** — {s['sugestao']}\n"
            f"   👤 {s.get('autor', '?')} • 📅 {s.get('data', '?')}\n\n"
        )
    txt += f"📊 **Total: {len(sugestoes_merda)} sugestões descartadas**"
    await message.reply_text(txt)

@app.on_message(filters.command("merda"))
@admin_only
async def cmd_merda(client, message):
    global backlog_sugestoes, sugestoes_merda
    
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Uso:** `/merda id_ou_texto, outro_id...`\n\n"
            "Remove sugestões ruins do backlog e guarda na pasta de lixo."
        )
        return
    
    argumentos = " ".join(message.command[1:]).split(',')

    buscas = [arg.strip().lower() for arg in argumentos if arg.strip()]
    
    removidas = []
    nao_encontradas = []
    
    for busca in buscas:
        encontradas = []
        if busca.isdigit():
            id_busca = int(busca)
            encontradas = [s for s in backlog_sugestoes if s.get('id') == id_busca]
        else:
            encontradas = [s for s in backlog_sugestoes if busca in s['sugestao'].lower()]
            
        if not encontradas:
            nao_encontradas.append(f"\"{busca}\" (Não encontrada)")
        elif len(encontradas) > 1:
            nao_encontradas.append(f"\"{busca}\" (Múltiplas encontradas)")
        else:
            if encontradas[0] not in removidas:
                removidas.append(encontradas[0])
                
    if not removidas:
        await message.reply_text(f"❌ Nenhuma sugestão pôde ser movida.\n" + ("\n".join(nao_encontradas) if nao_encontradas else ""))
        return
        
    ids_removidos = [s.get('id') for s in removidas]
    backlog_sugestoes = [s for s in backlog_sugestoes if s.get('id') not in ids_removidos]
    sugestoes_merda.extend(removidas)
    
    salvar_backlog(backlog_sugestoes)
    salvar_merda(sugestoes_merda)
    
    txt = f"💩 **{len(removidas)} Sugestão(ões) mandada(s) pro lixo!**\n\n"
    for s in removidas:
        txt += f"💩 #{s.get('id', '?')}: {s['sugestao']}\n"
        
    if nao_encontradas:
        txt += "\n⚠️ **Falhas:**\n"
        for n in nao_encontradas:
            txt += f"▫️ {n}\n"
            
    await message.reply_text(txt)

@app.on_message(filters.command("clearbacklog"))
@admin_only
async def cmd_clearbacklog(client, message):
    global backlog_sugestoes, sugestoes_merda
    
    if len(message.command) < 2 or message.command[1].lower() != "confirmar":
        await message.reply_text(
            "⚠️ **Atenção!** Isso vai mover **TODAS** as sugestões atuais para o lixo.\n\n"
            "Se você tem certeza, digite: `/clearbacklog confirmar`"
        )
        return
        
    if not backlog_sugestoes:
        await message.reply_text("📭 O backlog já está vazio.")
        return
        
    qtd = len(backlog_sugestoes)
    sugestoes_merda.extend(backlog_sugestoes)
    backlog_sugestoes = []
    
    salvar_backlog(backlog_sugestoes)
    salvar_merda(sugestoes_merda)
    
    await message.reply_text(f"🧹 **Backlog limpo!**\n\nForam movidas **{qtd}** sugestões para a lista de descartadas.")

# Handler para mensagens de texto durante criação de comando (agora com filtro específico)
@app.on_message(filters.text & ~filters.command(COMANDOS_INTERNOS) & filters.create(filtro_estado_usuario))
@admin_only
async def processar_criacao(client, message):
    user_id = message.from_user.id
    # A verificação 'if user_id not in user_states' não é mais necessária aqui
    
    state = user_states[user_id]
    etapa = state['etapa']
    dados = state['dados']
    
    if etapa == 'nome':
        nome = message.text.strip().lstrip('/')
        import re
        if not re.match(r'^[a-zA-Z0-9_]+$', nome):
            await message.reply_text("❌ Nome inválido! Use apenas letras, números e underlines (sem espaços ou acentos):")
            return
            
        aviso = ""
        if nome in comandos_personalizados:
            aviso = f"\n\n⚠️ **Aviso:** O comando `/{nome}` já existe e será **substituído** se você continuar!"
            
        dados['nome'] = nome
        user_states[user_id]['etapa'] = 'tipo'
        await message.reply_text(
            f"✅ Comando `/{nome}` definido!{aviso}\n\n"
            "Agora escolha o tipo de conteúdo:\n"
            "- Digite `texto` para enviar um texto\n"
            "- Envie uma **foto** para comando de foto\n"
            "- Envie um **vídeo** para comando de vídeo\n"
            "- Envie um **áudio/voz** para comando de áudio"
        )
    
    elif etapa == 'tipo' and message.text.lower() == 'texto':
        user_states[user_id]['etapa'] = 'conteudo_texto'
        await message.reply_text("✅ Tipo 'texto' definido. Agora envie o conteúdo do comando:")

    elif etapa == 'conteudo_texto':
        dados['conteudo'] = message.text
        dados['tipo'] = 'texto'
        user_states[user_id]['etapa'] = 'descricao'
        await message.reply_text(
            "✅ Conteúdo definido!\n\n"
            "Agora digite uma descrição para o comando (aparecerá no /menu):"
        )
    
    elif etapa == 'descricao':
        dados['descricao'] = message.text
        
        # Salva o comando
        comandos_personalizados[dados['nome']] = {
            'tipo': dados.get('tipo', 'texto'),
            'conteudo': dados.get('conteudo', ''),
            'media_id': dados.get('media_id'),
            'descricao': dados['descricao'],
            'criado_por': message.from_user.id,
            'data_criacao': str(datetime.now())
        }
        salvar_comandos(comandos_personalizados)
        await atualizar_menu_comandos(client)
        
        del user_states[user_id]
        
        await message.reply_text(
            f"✅ **Comando criado com sucesso!**\n\n"
            f"Use `/{dados['nome']}` para testar.\n"
            f"Veja no /menu que ele já aparece na lista!"
        )

# Filtro customizado para identificar comandos personalizados
async def filtro_comando_personalizado(_, __, message):
    if not message.text or not message.text.startswith('/'):
        return False
    # Pega o nome do comando sem a "/" e tira o @nomedobot se houver
    comando = message.text.split()[0][1:].split('@')[0].lower()
    # Retorna True se o comando NÃO for interno E estiver na lista de personalizados
    # Fazos lower() para garantir que case insensitive funcione (já que o JSON e o comando do Telegram usam lowercase)
    return comando not in COMANDOS_INTERNOS and comando in [c.lower() for c in comandos_personalizados.keys()]

@app.on_message(filters.create(filtro_comando_personalizado))
async def handle_custom_command(client, message):
    """Handler genérico para todos os comandos personalizados que funciona em tempo real."""
    comando_recebido = message.text.split()[0][1:].split('@')[0].lower()
    
    # Encontra a chave original correspondente ignorando case
    chave_real = next((c for c in comandos_personalizados if c.lower() == comando_recebido), None)
    
    if chave_real:
        await executar_comando_personalizado(client, message, chave_real, comandos_personalizados[chave_real])

@app.on_message((filters.photo | filters.video | filters.audio | filters.voice | filters.animation) & filters.create(filtro_estado_usuario))
@admin_only
async def processar_media_criacao(client, message):
    user_id = message.from_user.id
    # A verificação 'if user_id not in user_states' não é mais necessária aqui

    state = user_states[user_id]
    
    if state['etapa'] == 'tipo':
        dados = state['dados']
        
        if message.photo:
            dados['tipo'] = 'foto'
            dados['media_id'] = message.photo.file_id
        elif message.animation:
            dados['tipo'] = 'gif'
            dados['media_id'] = message.animation.file_id
        elif message.video:
            dados['tipo'] = 'video'
            dados['media_id'] = message.video.file_id
        elif message.audio:
            dados['tipo'] = 'audio'
            dados['media_id'] = message.audio.file_id
        elif message.voice:
            dados['tipo'] = 'voice'
            dados['media_id'] = message.voice.file_id
        
        # Corrige para salvar a legenda (caption) como conteúdo
        if message.caption:
            dados['conteudo'] = message.caption
        
        user_states[user_id]['etapa'] = 'descricao'
        await message.reply_text(
            "✅ Mídia recebida!\n\n"
            "Agora digite uma descrição para o comando (aparecerá no /menu):"
        )
    else:
        # Se não está criando comando, mas manda mídia, ignora ou avisa
        await message.reply_text("🤔 Para criar um comando com mídia, primeiro use /create e siga os passos.")

# -----------------------------------------
# NOTIFICAÇÃO DE ATUALIZAÇÃO
# -----------------------------------------
import asyncio

async def notificar_atualizacao():
    """Envia notificação nos grupos quando o bot reinicia após um git pull com mudanças."""
    await asyncio.sleep(5)  # Aguarda a conexão do bot estabilizar
    changelog_file = Path(CAMINHO_RAIZ_PROJETO) / "data" / "update_comandos.json"
    if not changelog_file.exists():
        return
    try:
        with open(changelog_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        commits = data.get('commits', [])
        if not commits:
            changelog_file.unlink(missing_ok=True)
            return

        txt = "🔄 **Bot de Comandos Atualizado!** 🚀\n\n"
        txt += "📋 **Mudanças nesta atualização:**\n"
        for c in commits:
            txt += f"• `{c['hash']}` — {c['message']}\n"
        txt += f"\n🕐 {data.get('updated_at', 'N/A')}"

        partes = dividir_texto_longo(txt)
        enviados = 0
        for grupo_id in GRUPOS_AUTORIZADOS:
            try:
                for parte in partes:
                    await app.send_message(grupo_id, parte)
                enviados += 1
            except Exception as e:
                log.error(f"Erro ao enviar notificação de update para {grupo_id}: {e}")

        changelog_file.unlink(missing_ok=True)
        log.info(f"Notificação de atualização enviada para {enviados} chat(s).")
    except Exception as e:
        log.error(f"Erro ao processar changelog de atualização: {e}")

if __name__ == "__main__":
    log.info("Bot de Comandos iniciando...")
    asyncio.get_event_loop().create_task(notificar_atualizacao())
    app.run()
