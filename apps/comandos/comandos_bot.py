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
COMANDOS_INTERNOS = ["start", "help", "menu", "id", "create", "list", "delete", "instance"]

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
        elif tipo == 'gif' and 'media_id' in info:
            await client.send_animation(message.chat.id, info['media_id'], caption=conteudo)
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
        "▫️ `/instance` - 🙏 Envia um GIF de bom dia abençoado\n\n"
    )
    
    if comandos_personalizados:
        txt += "**✨ Seus Comandos Personalizados:**\n"
        for cmd, info in comandos_personalizados.items():
            tipo_emoji = {'texto': '📝', 'foto': '🖼️', 'video': '🎬', 'audio': '🎵', 'gif': '🎞️'}.get(info.get('tipo'), '❓')
            txt += f"▫️ `/{cmd}` - {tipo_emoji} {info.get('descricao', 'Sem descrição')}\n"
    
    await message.reply_text(txt)

import random

@app.on_message(filters.command("instance"))
async def cmd_instance(client, message):
    chat_id = message.chat.id
    if GRUPOS_AUTORIZADOS and chat_id not in GRUPOS_AUTORIZADOS:
        return
        
    gifs_catolicos = [
        "https://media.tenor.com/tH2hPj0tK14AAAAC/bom-dia-deus.gif",
        "https://media.tenor.com/mO2X9g-T49QAAAAC/bom-dia.gif",
        "https://media.tenor.com/x4W9bNnsTlkAAAAC/bom-dia-catolico.gif",
        "https://media.tenor.com/1GvK_i8E0bIAAAAC/bom-dia-amigos.gif",
        "https://media.tenor.com/7s2NndU6_oYAAAAC/bom-dia-nossa-senhora.gif",
        "https://media.tenor.com/pZqM8lW_P7kAAAAC/bom-dia.gif",
        "https://media.tenor.com/r6_6gN9A0yQAAAAC/bom-dia.gif",
        "https://media.tenor.com/4h_R-A07_E4AAAAC/bom-dia.gif",
        "https://media.tenor.com/WvzldR40pgAAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/zhe-JWGvC3sAAAAM/jesus-god.gif",
        "https://media.tenor.com/TBeN43TlzMsAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/ESIuDKbQoLQAAAAM/bom-dia-bom-dia-retencao.gif",
        "https://media.tenor.com/t8U6B-f7AyUAAAAM/jesus-goodmorning.gif",
        "https://media.tenor.com/JdqOAJYXiRsAAAAM/bom-dia-que-o-nosso.gif",
        "https://media.tenor.com/N6XtQf397SwAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/2e614ZJXUq0AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/QJ55RdY_lnkAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/_yA0ZW7VjDYAAAAM/jesus-bible.gif",
        "https://media.tenor.com/kiJV0utmMoYAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/WbjAqTxVPloAAAAM/god-bless-you-jesus.gif",
        "https://media.tenor.com/VdoB274LopsAAAAM/bom-dia-valtatu%C3%AD-bom-dia.gif",
        "https://media.tenor.com/Swjz1L08ZR0AAAAM/bom-dia.gif",
        "https://media.tenor.com/EicZL9riOb0AAAAM/jesus-identidade.gif",
        "https://media.tenor.com/ozpe9Gew2zIAAAAM/good-morning.gif",
        "https://media.tenor.com/FRjJeOSeszkAAAAM/hand-jesus.gif",
        "https://media.tenor.com/MUNzKvQ6TI4AAAAM/good-morning-good-morning-jesus.gif",
        "https://media.tenor.com/meSKEFDMXl4AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/-et4j7grunEAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/EYMoO2zNXioAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/QqJt5KtuTAMAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/6E85v_z2UHcAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/uu_Aftj6DNQAAAAM/jesus-valtatui-hug.gif",
        "https://media.tenor.com/OQrAEaQ5f3YAAAAM/jesus-god.gif",
        "https://media.tenor.com/XJ9Wh-ffsIAAAAAM/kai-emo.gif",
        "https://media.tenor.com/WYPzgk5_10EAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/5yUdhK5oCq8AAAAM/buenos-dias-espiritu-santo.gif",
        "https://media.tenor.com/0w1Ml6Ee7sgAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/2j0mT6Xlyf8AAAAM/bom-dia-familia.gif",
        "https://media.tenor.com/Mto3IIa86WkAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/3Ss1pmJhxekAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/coq5gDS3jWQAAAAM/nossa-senhora-aparecida-aparecida.gif",
        "https://media.tenor.com/gGh7zghA0kgAAAAM/bom-dia-valtatui.gif",
        "https://media.tenor.com/4jJFGWmN7B8AAAAM/good-morning-summer.gif",
        "https://media.tenor.com/54C0cWeVEd8AAAAM/jesus-mother.gif",
        "https://media.tenor.com/OHVDsOM-6wcAAAAM/good-morning-bom-dia.gif",
        "https://media.tenor.com/D1fVXSAnO-EAAAAM/lord-jesus.gif",
        "https://media.tenor.com/83rYhEzFzpUAAAAM/wednesday-blessings.gif",
        "https://media.tenor.com/4Czk2atB5EIAAAAM/bom-dia-maria.gif",
        "https://media.tenor.com/cz49M8DXcfQAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/a4PHB6U5NlIAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/N-PhX-GrkeQAAAAM/buenos-dias-jesus-christ.gif",
        "https://media.tenor.com/gBI4Ooq9kMkAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/Ll-bZ48nGYQAAAAM/prayers-love.gif",
        "https://media.tenor.com/FnPx7siELT8AAAAM/good-morning-blessings.gif",
        "https://media.tenor.com/9PW3cKiWz7sAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/z2lK390bDDoAAAAM/good-morning-happy-day.gif",
        "https://media.tenor.com/2TM3BMUsEMUAAAAM/biblia.gif",
        "https://media.tenor.com/Ahp4XVJf-EYAAAAM/buenos-dias-sagrada-familia.gif",
        "https://media.tenor.com/mz53Yx8mY08AAAAM/good-morning.gif",
        "https://media.tenor.com/D_qHC9o075cAAAAM/bible-verses.gif",
        "https://media.tenor.com/1xBE49JBge4AAAAM/oracao-celebrate.gif",
        "https://media.tenor.com/U3EazesSHR4AAAAM/good-morning.gif",
        "https://media.tenor.com/gK-20Gwst94AAAAM/morning-good.gif",
        "https://media.tenor.com/rkGoEjoCoYcAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/MMgkZbcuMRkAAAAM/bom-dia-hdwan-prayer.gif",
        "https://media.tenor.com/Q3CdkNCz06sAAAAM/blessings-blessings-to-all.gif",
        "https://media.tenor.com/0zIkxBregbYAAAAM/bom-dia.gif",
        "https://media.tenor.com/xR8qBXdzTY8AAAAM/sunshine-sun.gif",
        "https://media.tenor.com/qeO5vYZI678AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/L8GdtgiH4AcAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/_A3LCp8xrvQAAAAM/good-morning-bestie.gif",
        "https://media.tenor.com/8RkyB4IBbtQAAAAM/bi-agua-viva.gif",
        "https://media.tenor.com/fYceF6gyd9UAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/UADRT6NYL0kAAAAM/bom-dia-valtatui-bom-dia-proc%C3%AA.gif",
        "https://media.tenor.com/NuHIaVTO-gkAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/9Re3YWwLBW4AAAAM/para-hoje-muito-amor-e-ora%C3%A7ao.gif",
        "https://media.tenor.com/PtUpt4bYONYAAAAM/jesus-valtatui.gif",
        "https://media.tenor.com/PWTG8PeVKnwAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/rPP-dfRJ3dYAAAAM/bendiciones-am%C3%A9n-sagrado-corazon-de-jesus.gif",
        "https://media.tenor.com/bwxIBum19HYAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/0dokFblgK8YAAAAM/bom-dia.gif",
        "https://media.tenor.com/076pcmPlQScAAAAM/morning.gif",
        "https://media.tenor.com/8Oxj3l6awx8AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/D-nTAC6AZKsAAAAM/azucrim-bom-dia.gif",
        "https://media.tenor.com/Pl7Xuy82DnUAAAAM/good-morning-son-good-morning.gif",
        "https://media.tenor.com/y6jmYdkxXjoAAAAM/bible-bible-verse.gif",
        "https://media.tenor.com/xCm3UyMjNeUAAAAM/good-morning-good-morning-prayer.gif",
        "https://media.tenor.com/tRH_Cw1gffcAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/QEhIxc5z4p4AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/cOGLft8aqDQAAAAM/virgin-mary-hearts.gif",
        "https://media.tenor.com/nBQIQ91V6E4AAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/iREk9g3QAtgAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/fOlwsO7sM6sAAAAM/jesus-buenos-dias.gif",
        "https://media.tenor.com/PAk6_gC4mQQAAAAM/jesus-senhor.gif",
        "https://media.tenor.com/YZeKqKkWlW8AAAAM/good-morning.gif",
        "https://media.tenor.com/BWssbWIiVzEAAAAM/jesus-buenos-dias-amen-oracion-alma.gif",
        "https://media.tenor.com/EE4N5dJyNeMAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/p4lE6h7c7rQAAAAM/goodmorning-jesus.gif",
        "https://media.tenor.com/gNIzMvmmnj8AAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/skFX8sbM8uEAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/HFymbbW5olQAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/Im4IIdE2UpkAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/ui7xNPJZwAwAAAAM/good-morning-bom-dia.gif",
        "https://media.tenor.com/9c3JfC7N-LUAAAAM/bom-dia.gif",
        "https://media.tenor.com/c5U5d-T5f48AAAAM/sagrado-corazon-jesus-catolico.gif",
        "https://media.tenor.com/Jr2IphpdQKgAAAAM/dia-de-nossa-senhora-da-concei%C3%A7%C3%A3o-feliz-dia-de-nossa-senhora-da-concei%C3%A7%C3%A3o.gif",
        "https://media.tenor.com/oZyx9ACPK6AAAAAM/josimo-josimo-jesus.gif",
        "https://media.tenor.com/QI0mWevnoKQAAAAM/goodnight-prayer.gif",
        "https://media.tenor.com/JffRvTDkqdIAAAAM/telefonemas-da-esperan%C3%A7a-good-morning.gif",
        "https://media.tenor.com/eaKqCHCYpqoAAAAM/thank-you-jesus-ty-jesus.gif",
        "https://media.tenor.com/MEyFxlV7CWgAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/p8cfldA_ibsAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/weSsoxE4Ek4AAAAM/primeiro-lugar.gif",
        "https://media.tenor.com/emWB2TBI4KkAAAAM/te-amo-bom-dia.gif",
        "https://media.tenor.com/kvaDNhFUZEMAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/9wcBlmR5RpAAAAAM/bom-dia-valtatui-valtatui.gif",
        "https://media.tenor.com/QrVW4rFivTUAAAAM/feliz-dia-bom-dia.gif",
        "https://media.tenor.com/medTegOK7bUAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/vtybKxmPWRIAAAAM/bom-dia-good-morning.gif",
        "https://media.tenor.com/QebHrvlN75QAAAAM/bom-dia.gif",
        "https://media.tenor.com/r9IgggRp3QsAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/Hkr34fWLgCwAAAAM/bom-dia-pedro-soares.gif",
        "https://media.tenor.com/v507l5QWDFIAAAAM/bom-dia-dia-aben%C3%A7oado.gif",
        "https://media.tenor.com/jWID6ncHXiEAAAAM/paradise.gif",
        "https://media.tenor.com/_Qby_0WHpqUAAAAM/good-morning.gif",
        "https://media.tenor.com/Lju1LDs_BTcAAAAM/bom-dia-good-day.gif",
        "https://media.tenor.com/eM1POLklyhEAAAAM/bom-dia-valtatui.gif",
        "https://media.tenor.com/5FWV9jC2cewAAAAM/um-bom-dia-ola.gif",
        "https://media.tenor.com/06s6ze1DOiIAAAAM/buenos-dias-jesus-christ.gif",
        "https://media.tenor.com/8BOWxoteyBIAAAAM/good-morning.gif",
        "https://media.tenor.com/WhajuVYZRKsAAAAM/aleluia-gloria.gif"
    ]
    gif_escolhido = random.choice(gifs_catolicos)
    try:
        await client.send_animation(message.chat.id, gif_escolhido, reply_to_message_id=message.id)
    except Exception as e:
        log.error(f"Erro ao enviar gif /instance: {e}")

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

@app.on_message((filters.photo | filters.video | filters.audio | filters.animation) & filters.create(filtro_estado_usuario))
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
