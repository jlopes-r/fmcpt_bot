# FMCPT Bot

Bot multiplataforma para download de mídias do Telegram com suporte a Twitter/X, YouTube, Instagram, TikTok, Threads e Pinterest.

## 🚀 Funcionalidades

- **Download universal** de vídeos e imagens via yt-dlp
- **Instagram** com múltiplos fallbacks (cookies, embed, Instaloader, API externa)
- **Twitter/X** via API vxtwitter para carrosséis e vídeos
- **Detecção de links duplicados** com "Boca de Leite" 🥛
- **Rate limiting** e segurança por grupo
- **Estatísticas** de uso em tempo real
- **Ranking de vacilos** (links repetidos)

## 📋 Pré-requisitos

- Python 3.10+
- Conta no Telegram (Bot Token via @BotFather)
- Cookies do Instagram (para download autenticado - veja `COOKIES_SETUP.md`)

## 🔧 Instalação

```bash
# Clonar repositório
git clone https://github.com/SEU_USUARIO/fmcpt_bot.git
cd fmcpt_bot

# Criar ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows

# Instalar dependências
pip install -r apps/telegram_bot/requirements.txt
```

## ⚙️ Configuração

1. Copie o arquivo de exemplo e configure:
```bash
cp apps/telegram_bot/.env.example apps/telegram_bot/.env
```

2. Edite `apps/telegram_bot/.env` com suas credenciais:
```
API_ID=seu_api_id
API_HASH=seu_api_hash
BOT_TOKEN=seu_bot_token
GRUPOS_AUTORIZADOS=123456789,-987654321
MODO_ZUEIRA=1
IG_USERNAME=seu_usuario_instagram
IG_PASSWORD=sua_senha_instagram
```

3. (Opcional) Configure cookies do Instagram seguindo `COOKIES_SETUP.md`

## 🏃 Execução

```bash
python apps/telegram_bot/super_bot.py
```

## 📁 Estrutura do Projeto

```
fmcpt_bot/
├── apps/
│   └── telegram_bot/          # Bot principal do Telegram
│       ├── super_bot.py        # Entry point
│       ├── instagram_extractor.py  # Extrator multi-método
│       └── requirements.txt
├── packages/
│   └── database/              # Gerenciamento SQLite
├── scripts/                   # Utilitários de deploy e manutenção
├── data/                      # Dados persistentes (ignorado pelo git)
│   ├── downloads/             # Downloads temporários
│   ├── logs/                  # Logs da aplicação
│   └── sessions/              # Sessões do Pyrogram
└── assets/                    # Arquivos estáticos (áudios, imagens)
```

## 🔒 Segurança

- Arquivos sensíveis (`.env`, `*.session`, `*.db`, cookies) são ignorados pelo `.gitignore`
- Credenciais não são hardcoded (usam variáveis de ambiente)
- Rate limiting para evitar abuso
- Validação de domínios permitidos

## 📊 Comandos do Bot

- `/id` - Mostra o ID do chat
- `/stats` - Estatísticas de uso (RAM, CPU, downloads)
- `/comi` - Modo zoeira (escolhe membro aleatório)

## 🛠️ Manutenção

### Renewal de Cookies Instagram
```bash
python scripts/renew_ig_cookies.py
```

### Deploy para GCP
```powershell
.\scripts\deploy.ps1
```

## 📄 Licença

MIT

## 🤝 Contribuição

Pull requests são bem-vindos! Para mudanças maiores, abra uma issue primeiro.

---

**Nota:** Este bot foi desenvolvido para fins educacionais e de automação pessoal. Respeite os Termos de Serviço das plataformas utilizadas.
