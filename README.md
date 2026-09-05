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

### Tradução automática e perfis

A tradução continua usando `deep-translator` e a detecção local com `langdetect`,
sem chave de API ou serviço pago adicional. Textos em português e inglês são
preservados. No X, o bot considera o idioma informado pela fonte e analisa tweet
e citação separadamente. Sem informação suficiente, mantém o original: a
detecção exige pelo menos 20 letras e 4 palavras em textos latinos, confiança
de 95% e concordância com o idioma da fonte quando disponível. Isso reduz
traduções indevidas, mas também deixa algumas frases estrangeiras curtas sem
tradução. Links, menções, hashtags e emojis são preservados. O serviço de tradução
continua dependendo de disponibilidade externa; se falhar, o original é mantido.

Os cards do Instagram usam fontes escaláveis, avatar circular e altura ajustada
ao conteúdo. A coleta complementa dados parciais com os métodos disponíveis;
campos que o Instagram não disponibilizou aparecem como indisponíveis, sem
confundir falha de coleta com bio vazia. A disponibilidade de foto e dados ainda
depende das respostas do Instagram e dos cookies configurados.

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
BOT_TOKEN_COMANDOS=seu_bot_token_de_comandos
GRUPOS_AUTORIZADOS=123456789,-987654321
MODO_ZUEIRA=1
IG_USERNAME=seu_usuario_instagram
IG_PASSWORD=sua_senha_instagram
MINI_APP_URL=https://sua-url-do-mini-app
```

3. (Opcional) Configure cookies do Instagram seguindo `COOKIES_SETUP.md`

### Limites opcionais

```env
# Limita concorrencia e uso de disco/RAM nos downloads do Instagram.
MAX_DOWNLOADS=3
IG_MEDIA_DOWNLOAD_CONCURRENCY=3
IG_MAX_CAROUSEL_ITEMS=20
MAX_MEDIA_BYTES=2000000000
PROFILE_PICTURE_MAX_BYTES=10485760

# So habilite se o Mini App estiver atras de um proxy que controla X-Forwarded-For.
MINI_APP_TRUST_PROXY_HEADERS=1
MINI_APP_MEMBERSHIP_CACHE_TTL=120
```

## 🏃 Execução

```bash
python apps/telegram_bot/super_bot.py
python apps/comandos/comandos_bot.py
```

### Mini App Telegram

O painel web fica em `apps/mini_app`, mas nao deve ser publicado como pasta
estatica aberta. Rode `apps/mini_app_server/server.py` atras de um proxy HTTPS e
configure `MINI_APP_URL` no `.env`. O servidor valida `Telegram.WebApp.initData`
e so libera `catalog.json` para usuarios que pertencem a algum chat em
`GRUPOS_AUTORIZADOS`.

Exemplo local do servidor:

```bash
python -m apps.mini_app_server.server --host 127.0.0.1 --port 8080
```

Sem `MINI_APP_URL`, o comando `/menu` mantém o menu textual como fallback.

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

- `/menu` - Abre o painel de comandos ou mostra o fallback textual
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

### Testes
```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

O deploy busca o branch `main` pela VM configurada no script. Cookies e `.env`
permanecem apenas no servidor.

## 📄 Licença

MIT

## 🤝 Contribuição

Pull requests são bem-vindos! Para mudanças maiores, abra uma issue primeiro.

---

**Nota:** Este bot foi desenvolvido para fins educacionais e de automação pessoal. Respeite os Termos de Serviço das plataformas utilizadas.
