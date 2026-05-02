import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

from functools import wraps
from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio
from dotenv import load_dotenv

# Configuração
# O caminho para o arquivo .env é definido de forma absoluta para garantir
# que o bot funcione corretamente quando executado como um serviço na VM.
CAMINHO_ENV = "/home/juanl/fmcpt_bot/apps/telegram_bot/.env"
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

# Estado da conversa para criar comandos
user_states = {}

# Lista de comandos internos que o bot reconhece nativamente
COMANDOS_INTERNOS = ["start", "help", "menu", "id", "create", "list", "delete"]

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
        
        if tipo == 'texto':
            await message.reply_text(conteudo)
        elif tipo == 'foto' and 'media_id' in info:
            await client.send_photo(message.chat.id, info['media_id'], caption=conteudo)
        elif tipo == 'video' and 'media_id' in info:
            await client.send_video(message.chat.id, info['media_id'], caption=conteudo)
        elif tipo == 'audio' and 'media_id' in info:
            await client.send_audio(message.chat.id, info['media_id'], caption=conteudo)
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
        "▫️ `/delete NOME` - 🗑️ Deleta um comando\n\n"
    )
    
    if comandos_personalizados:
        txt += "**✨ Seus Comandos Personalizados:**\n"
        for cmd, info in comandos_personalizados.items():
            tipo_emoji = {'texto': '📝', 'foto': '🖼️', 'video': '🎬', 'audio': '🎵'}.get(info.get('tipo'), '❓')
            txt += f"▫️ `/{cmd}` - {tipo_emoji} {info.get('descricao', 'Sem descrição')}\n"
    
    await message.reply_text(txt)

@app.on_message(filters.command("id"))
async def cmd_id(client, message):
    await message.reply_text(f"O ID desta conversa é: `{message.chat.id}`")

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
        "Digite o nome do comando (sem a barra, ex: `frias`):"
    )

@app.on_message(filters.command("list"))
@admin_only
async def cmd_list(client, message):
    if not comandos_personalizados:
        await message.reply_text("📭 Nenhum comando personalizado criado.")
        return
    
    txt = "**📋 Comandos Personalizados:**\n\n"
    for cmd, info in comandos_personalizados.items():
        txt += f"- `/{cmd}` - {info.get('descricao', 'Sem descrição')}\n"
        txt += f"  Tipo: {info.get('tipo', 'texto')}\n\n"
    
    await message.reply_text(txt)

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
        await message.reply_text(f"✅ Comando `/{cmd_nome}` deletado com sucesso!")
    else:
        await message.reply_text(f"❌ Comando `/{cmd_nome}` não encontrado.")

# Filtro para verificar se o usuário está no processo de criação de um comando
async def filtro_estado_usuario(_, __, message):
    return message.from_user.id in user_states

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
        if not nome.isalnum():
            await message.reply_text("❌ Nome inválido! Use apenas letras e números:")
            return
        dados['nome'] = nome
        user_states[user_id]['etapa'] = 'tipo'
        await message.reply_text(
            f"✅ Comando `/{nome}` definido!\n\n"
            "Agora escolha o tipo de conteúdo:\n"
            "- Digite `texto` para enviar um texto\n"
            "- Envie uma **foto** para comando de foto\n"
            "- Envie um **vídeo** para comando de vídeo\n"
            "- Envie um **áudio** para comando de áudio"
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
    # Pega o nome do comando sem a "/"
    comando = message.text.split()[0][1:]
    # Retorna True se o comando NÃO for interno E estiver na lista de personalizados
    return comando not in COMANDOS_INTERNOS and comando in comandos_personalizados

@app.on_message(filters.create(filtro_comando_personalizado))
async def handle_custom_command(client, message):
    """Handler genérico para todos os comandos personalizados que funciona em tempo real."""
    comando = message.text.split()[0][1:]
    if comando in comandos_personalizados:
        await executar_comando_personalizado(client, message, comando, comandos_personalizados[comando])

@app.on_message(filters.photo | filters.video | filters.audio & filters.create(filtro_estado_usuario))
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
        elif message.video:
            dados['tipo'] = 'video'
            dados['media_id'] = message.video.file_id
        elif message.audio:
            dados['tipo'] = 'audio'
            dados['media_id'] = message.audio.file_id
        
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

if __name__ == "__main__":
    log.info("Bot de Comandos iniciando...")
    app.run()
