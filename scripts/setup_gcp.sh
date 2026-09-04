#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Atualiza o sistema
sudo apt-get update && sudo apt-get upgrade -y

# Instala Python e FFmpeg (essencial para o yt-dlp)
sudo apt-get install -y python3-pip python3-venv ffmpeg

# Cria um ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instala as dependências do bot
pip install --upgrade pip
pip install -r apps/telegram_bot/requirements.txt

echo "------------------------------------------------"
echo "✅ Setup concluído!"
echo "Agora você precisa configurar o arquivo .env em apps/telegram_bot/"
echo "E rodar o bot com: source venv/bin/activate && python apps/telegram_bot/super_bot.py"
echo "------------------------------------------------"
