#!/bin/bash
# ==============================================
# Script de Atualização - VM
# ==============================================
# Uso: cd ~/fmcpt_bot && ./scripts/update.sh
#
# Faz git pull, detecta mudanças, gera changelog
# e reinicia os bots automaticamente.
# ==============================================

# Configuração - ajuste conforme necessário
REPO_DIR="/home/juanl/fmcpt_bot"
SUPERBOT_SERVICE="superbot.service"
COMANDOS_SERVICE="comandosbot.service"

cd "$REPO_DIR" || { echo "❌ Diretório $REPO_DIR não encontrado!"; exit 1; }

echo "================================================"
echo "🔄 Verificando atualizações..."
echo "================================================"
echo ""

# Salva o HEAD atual antes do pull
OLD_HEAD=$(git rev-parse HEAD 2>/dev/null)

if [ -z "$OLD_HEAD" ]; then
    echo "❌ Erro: Não é um repositório git válido."
    exit 1
fi

# Faz o pull
echo "📥 Executando git pull..."
git pull

# Pega o novo HEAD
NEW_HEAD=$(git rev-parse HEAD 2>/dev/null)

# Verifica se houve mudanças
if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
    echo ""
    echo "✅ Nenhuma atualização encontrada. Tudo está atualizado!"
    echo ""
    
    # Pergunta se quer reiniciar mesmo assim
    read -p "Deseja reiniciar os bots mesmo assim? (s/N) " resposta
    if [ "$resposta" = "s" ] || [ "$resposta" = "S" ]; then
        echo "🔄 Reiniciando serviços..."
        sudo systemctl restart "$SUPERBOT_SERVICE" 2>/dev/null
        sudo systemctl restart "$COMANDOS_SERVICE" 2>/dev/null
        echo "✅ Bots reiniciados!"
    fi
    exit 0
fi

echo ""
echo "🆕 Atualizações detectadas!"
echo ""

# Mostra os commits novos no terminal
echo "📋 Commits novos:"
git log --oneline --no-merges ${OLD_HEAD}..${NEW_HEAD}
echo ""

# Gera os arquivos de changelog
CHANGELOG=$(git log --oneline --no-merges ${OLD_HEAD}..${NEW_HEAD})

# Verifica quais arquivos mudaram para notificar apenas o bot correto
CHANGED_FILES=$(git diff --name-only ${OLD_HEAD}..${NEW_HEAD})

export MUDOU_SUPERBOT=false
export MUDOU_COMANDOS=false

if echo "$CHANGED_FILES" | grep -qE "apps/telegram_bot|packages/|assets/|scripts/|data/"; then
    export MUDOU_SUPERBOT=true
fi

if echo "$CHANGED_FILES" | grep -qE "apps/comandos"; then
    export MUDOU_COMANDOS=true
fi

# Se nada específico bateu, notifica o principal por padrão
if [ "$MUDOU_SUPERBOT" = "false" ] && [ "$MUDOU_COMANDOS" = "false" ]; then
    export MUDOU_SUPERBOT=true
fi

echo "$CHANGELOG" | python3 -c "
import json, sys, os
from datetime import datetime, timezone, timedelta

commits = []
for line in sys.stdin:
    line = line.strip()
    if line:
        parts = line.split(' ', 1)
        commits.append({
            'hash': parts[0],
            'message': parts[1] if len(parts) > 1 else '(sem mensagem)'
        })

tz_br = timezone(timedelta(hours=-3))
data = {
    'commits': commits,
    'updated_at': datetime.now(tz_br).strftime('%d/%m/%Y às %H:%M')
}

if os.environ.get('MUDOU_SUPERBOT') == 'true':
    with open('data/update_superbot.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if os.environ.get('MUDOU_COMANDOS') == 'true':
    with open('data/update_comandos.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

print(f'📋 {len(commits)} commit(s) processado(s). SuperBot={os.environ.get(\"MUDOU_SUPERBOT\")}, Comandos={os.environ.get(\"MUDOU_COMANDOS\")}')
"

# Reinicia os serviços
echo "🔄 Reiniciando serviços..."
sudo systemctl restart "$SUPERBOT_SERVICE" 2>/dev/null && echo "  ✅ $SUPERBOT_SERVICE reiniciado" || echo "  ⚠️  $SUPERBOT_SERVICE não encontrado ou falhou"
sudo systemctl restart "$COMANDOS_SERVICE" 2>/dev/null && echo "  ✅ $COMANDOS_SERVICE reiniciado" || echo "  ⚠️  $COMANDOS_SERVICE não encontrado ou falhou"

echo ""
echo "================================================"
echo "✅ Atualização concluída com sucesso!"
echo "================================================"
echo ""
echo "Os bots enviarão a notificação de atualização"
echo "nos grupos automaticamente ao iniciar."
echo ""
