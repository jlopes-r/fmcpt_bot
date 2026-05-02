import os
import sys
import logging
from datetime import datetime

from pyrogram import Client, filters
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

# Cliente
app = Client(
    "meu_comandos_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start"))
async def cmd_start(client, message):
    await message.reply_text("🤖 Bot de Comandos iniciado! Use /help para ver os comandos.")

@app.on_message(filters.command("help"))
async def cmd_help(client, message):
    txt = (
        "**📋 Comandos disponíveis:**\n\n"
        "- `/start` - Inicia o bot\n"
        "- `/help` - Mostra esta mensagem\n"
        "- `/echo <texto>` - Repete o texto enviado"
    )
    await message.reply_text(txt)

@app.on_message(filters.command("echo"))
async def cmd_echo(client, message):
    texto = " ".join(message.command[1:]) if len(message.command) > 1 else "📢 Nada para ecoar!"
    await message.reply_text(f"🔊 {texto}")

if __name__ == "__main__":
    log.info("Bot de Comandos iniciando...")
    app.run()
