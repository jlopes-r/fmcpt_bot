window.FMCPT_CATALOG = {
  "bots": [
    {
      "id": "super",
      "name": "Super Bot",
      "description": "Downloads, rankings, castigos e utilidades do grupo.",
      "commands": [
        {
          "name": "menu",
          "category": "Interface",
          "description": "Abre o painel de comandos",
          "aliases": [
            "help"
          ],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "help",
          "category": "Interface",
          "description": "Mostra o guia organizado por categoria",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "ranking",
          "category": "Rankings",
          "description": "Ranking semanal de links repetidos",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "bocadeleite",
          "category": "Rankings",
          "description": "Podio do mes atual",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "anual",
          "category": "Rankings",
          "description": "Hall da fama do ano",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "repetido",
          "category": "Castigo",
          "description": "Castigo manual respondendo a mensagem de alguem",
          "aliases": [],
          "adminOnly": false,
          "usage": "/repetido"
        },
        {
          "name": "bloq",
          "category": "Castigo",
          "description": "Bloqueia temporariamente quem enviou link ruim",
          "aliases": [],
          "adminOnly": false,
          "usage": "/bloq @usuario"
        },
        {
          "name": "comi",
          "category": "Diversao",
          "description": "Escolhe uma vitima aleatoria do grupo",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "id",
          "category": "Utilidades",
          "description": "Mostra o ID deste chat",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "stats",
          "category": "Utilidades",
          "description": "Mostra status tecnico do bot",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "ping",
          "category": "Utilidades",
          "description": "Verifica se o bot esta online",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "retry",
          "category": "Utilidades",
          "description": "Tenta novamente um download que falhou",
          "aliases": [],
          "adminOnly": false,
          "usage": "/retry em resposta ao erro"
        },
        {
          "name": "sync",
          "category": "Administracao",
          "description": "Sincroniza o autocomplete do Telegram",
          "aliases": [],
          "adminOnly": true,
          "usage": ""
        },
        {
          "name": "update_ytdlp",
          "category": "Administracao",
          "description": "Atualiza o yt-dlp manualmente",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "ig_status",
          "category": "Administracao",
          "description": "Verifica a validade dos cookies do Instagram",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "ig_renew",
          "category": "Administracao",
          "description": "Gera cookies novos do Instagram via auto-login",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        }
      ]
    },
    {
      "id": "comandos",
      "name": "Comandos Bot",
      "description": "Comandos personalizados, GIFs e backlog.",
      "commands": [
        {
          "name": "start",
          "category": "Interface",
          "description": "Inicia o bot de comandos",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "menu",
          "category": "Interface",
          "description": "Abre o painel de comandos",
          "aliases": [
            "help"
          ],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "help",
          "category": "Interface",
          "description": "Mostra o guia organizado por categoria",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "list",
          "category": "Comandos personalizados",
          "description": "Lista comandos personalizados",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "create",
          "category": "Comandos personalizados",
          "description": "Cria um comando personalizado",
          "aliases": [],
          "adminOnly": true,
          "usage": ""
        },
        {
          "name": "delete",
          "category": "Comandos personalizados",
          "description": "Apaga um comando personalizado",
          "aliases": [],
          "adminOnly": true,
          "usage": "/delete nome"
        },
        {
          "name": "instance",
          "category": "GIFs",
          "description": "Envia um GIF de bom dia abencoado",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "duvida",
          "category": "GIFs",
          "description": "Envia um GIF de duvida",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "add",
          "category": "GIFs",
          "description": "Adiciona GIF respondendo a uma animacao",
          "aliases": [],
          "adminOnly": true,
          "usage": "/add instance|duvida"
        },
        {
          "name": "removegif",
          "category": "GIFs",
          "description": "Remove GIF respondendo a uma animacao",
          "aliases": [],
          "adminOnly": true,
          "usage": ""
        },
        {
          "name": "gifstats",
          "category": "GIFs",
          "description": "Mostra estatisticas das bases de GIFs",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        },
        {
          "name": "backlog",
          "category": "Backlog",
          "description": "Lista, cria e gerencia sugestoes",
          "aliases": [],
          "adminOnly": false,
          "usage": "/backlog texto|done|merda|lixeira|limpar"
        },
        {
          "name": "sync",
          "category": "Administracao",
          "description": "Sincroniza o autocomplete do Telegram",
          "aliases": [],
          "adminOnly": true,
          "usage": ""
        },
        {
          "name": "cancelar",
          "category": "Administracao",
          "description": "Cancela criacao/edicao em andamento",
          "aliases": [],
          "adminOnly": true,
          "usage": ""
        },
        {
          "name": "id",
          "category": "Utilidades",
          "description": "Mostra o ID deste chat",
          "aliases": [],
          "adminOnly": false,
          "usage": ""
        }
      ],
      "customCommands": {},
      "customCategories": []
    }
  ]
};
