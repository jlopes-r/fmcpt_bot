#!/bin/bash

# Atualiza o sistema
sudo apt-get update && sudo apt-get upgrade -y

# Instala Python e FFmpeg (essencial para o yt-dlp)
sudo apt-get install -y python3-pip python3-venv ffmpeg

# Cria um ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instala as dependências do bot
pip install --upgrade pip
pip install -r apps/telegram-bot/requirements.txt

echo "------------------------------------------------"
echo "✅ Setup concluído!"
echo "Agora você precisa configurar o arquivo .env em apps/telegram-bot/"
echo "E rodar o bot com: source venv/bin/activate && python apps/telegram-bot/super_bot.py"
echo "------------------------------------------------"
