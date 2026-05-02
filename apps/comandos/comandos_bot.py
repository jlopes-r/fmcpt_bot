import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio
from dotenv import load_dotenv

# Configuração
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(RAIZ, "apps", "telegram_bot", ".env"))

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN_COMANDOS") or os.getenv("BOT_TOKEN")

# Logging
log = logging.getLogger("ComandosBot")
logging.basicConfig(level=logging.INFO)

# Caminho para arquivo de comandos personalizados
COMANDOS_FILE = Path(RAIZ) / "data" / "comandos_personalizados.json"

# Estado da conversa para criar comandos
user_states = {}

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

async def registrar_comandos_dinamicos():
    """Registra handlers para comandos personalizados"""
    for cmd_nome, cmd_info in comandos_personalizados.items():
        # Cria handler dinâmico
        async def handler(client, message, nome=cmd_nome, info=cmd_info):
            await executar_comando_personalizado(client, message, nome, info)
        
        # Registra o handler
        app.add_handler(filters.command(cmd_nome))
        # Armazena referência
        setattr(app, f'cmd_dinamico_{cmd_nome}', handler)

async def executar_comando_personalizado(client, message, nome, info):
    """Executa um comando personalizado"""
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
    await message.reply_text("🤖 Bot de Comandos iniciado! Use /help para ver os comandos.")

@app.on_message(filters.command("help"))
async def cmd_help(client, message):
    txt = (
        "**📋 Comandos disponíveis:**\n\n"
        "- `/start` - Inicia o bot\n"
        "- `/help` - Mostra esta mensagem\n"
        "- `/create` - Criar novo comando personalizado\n"
        "- `/list` - Lista comandos personalizados\n"
        "- `/delete <comando>` - Deleta um comando personalizado\n\n"
    )
    
    if comandos_personalizados:
        txt += "**Comandos personalizados:**\n"
        for cmd, info in comandos_personalizados.items():
            txt += f"- `/{cmd}` - {info.get('descricao', 'Sem descrição')}\n"
    
    await message.reply_text(txt)

@app.on_message(filters.command("create"))
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
async def cmd_delete(client, message):
    if len(message.command) < 2:
        await message.reply_text("❌ Uso: `/delete <nome_do_comando>`")
        return
    
    cmd_nome = message.command[1].lstrip('/')
    
    if cmd_nome in comandos_personalizados:
        del comandos_personalizados[cmd_nome]
        salvar_comandos(comandos_personalizados)
        await message.reply_text(f"✅ Comando `/{cmd_nome}` deletado com sucesso!")
    else:
        await message.reply_text(f"❌ Comando `/{cmd_nome}` não encontrado.")

# Handler para mensagens durante criação de comando
@app.on_message(filters.text & ~filters.command(["start", "help", "create", "list", "delete", "echo"]))
async def processar_criacao(client, message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
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
    
    elif etapa == 'tipo':
        await message.reply_text("❌ Envie o conteúdo conforme o tipo desejado (foto, vídeo ou áudio) ou digite `texto` para comando de texto.")
    
    elif etapa == 'conteudo_texto':
        dados['conteudo'] = message.text
        dados['tipo'] = 'texto'
        user_states[user_id]['etapa'] = 'descricao'
        await message.reply_text(
            "✅ Conteúdo definido!\n\n"
            "Agora digite uma descrição para o comando (aparecerá no /help):"
        )
    
    elif etapa == 'descricao':
        dados['descricao'] = message.text
        
        # Salva o comando
        comandos_personalizados[dados['nome']] = {
            'tipo': dados['tipo'],
            'conteudo': dados.get('conteudo', ''),
            'media_id': dados.get('media_id'),
            'descricao': dados['descricao'],
            'criado_por': message.from_user.id,
            'data_criacao': str(datetime.now())
        }
        salvar_comandos(comandos_personalizados)
        
        # Registra o comando dinamicamente
        await registrar_comandos_dinamicos()
        
        del user_states[user_id]
        
        await message.reply_text(
            f"✅ **Comando criado com sucesso!**\n\n"
            f"Use `/{dados['nome']}` para testar.\n"
            f"Veja no `/help` que ele já aparece no menu!"
        )

@app.on_message(filters.photo | filters.video | filters.audio)
async def processar_media_criacao(client, message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    if state['etapa'] == 'tipo':
        dados = state['dados']
        
        if message.photo:
            dados['tipo'] = 'foto'
            # Salva o file_id da foto
            dados['media_id'] = message.photo.file_id
        elif message.video:
            dados['tipo'] = 'video'
            dados['media_id'] = message.video.file_id
        elif message.audio:
            dados['tipo'] = 'audio'
            dados['media_id'] = message.audio.file_id
        
        user_states[user_id]['etapa'] = 'descricao'
        await message.reply_text(
            "✅ Mídia recebida!\n\n"
            "Agora digite uma descrição para o comando (aparecerá no /help):"
        )
    else:
        # Se não está criando comando, processa como texto
        if state['etapa'] == 'conteudo_texto':
            await processar_criacao(client, message)

if __name__ == "__main__":
    log.info("Bot de Comandos iniciando...")
    # Registra comandos existentes
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(registrar_comandos_dinamicos())
    app.run()
