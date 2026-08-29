from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    category: str
    aliases: tuple[str, ...] = ()
    admin_only: bool = False
    autocomplete: bool = True
    mini_app: bool = True
    ephemeral: bool = False
    usage: str = ""
    payload: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


SUPER_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("menu", "Abre o painel de comandos", "Interface", aliases=("help",), payload="open_menu", ephemeral=True),
    CommandSpec("help", "Mostra o guia organizado por categoria", "Interface", autocomplete=False, ephemeral=True),
    CommandSpec("ranking", "Ranking semanal de links repetidos", "Rankings"),
    CommandSpec("bocadeleite", "Podio do mes atual", "Rankings"),
    CommandSpec("anual", "Hall da fama do ano", "Rankings"),
    CommandSpec("repetido", "Castigo manual respondendo a mensagem de alguem", "Castigo", usage="/repetido"),
    CommandSpec("bloq", "Bloqueia temporariamente quem enviou link ruim", "Castigo", usage="/bloq @usuario"),
    CommandSpec("comi", "Escolhe uma vitima aleatoria do grupo", "Diversao"),
    CommandSpec("id", "Mostra o ID deste chat", "Utilidades", ephemeral=True),
    CommandSpec("stats", "Mostra status tecnico do bot", "Utilidades", ephemeral=True),
    CommandSpec("ping", "Verifica se o bot esta online", "Utilidades"),
    CommandSpec("retry", "Tenta novamente um download que falhou", "Utilidades", usage="/retry em resposta ao erro"),
    CommandSpec("sync", "Sincroniza o autocomplete do Telegram", "Administracao", admin_only=True, ephemeral=True),
    CommandSpec("update_ytdlp", "Atualiza o yt-dlp manualmente", "Administracao", admin_only=True, ephemeral=True),
    CommandSpec("ig_status", "Verifica a validade dos cookies do Instagram", "Administracao", admin_only=True, ephemeral=True),
    CommandSpec("ig_renew", "Gera cookies novos do Instagram via auto-login", "Administracao", admin_only=True, ephemeral=True),
)


COMANDOS_BOT_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("start", "Inicia o bot de comandos", "Interface"),
    CommandSpec("menu", "Abre o painel de comandos", "Interface", aliases=("help",), payload="open_menu", ephemeral=True),
    CommandSpec("help", "Mostra o guia organizado por categoria", "Interface", autocomplete=False, ephemeral=True),
    CommandSpec("list", "Lista comandos personalizados", "Comandos personalizados", ephemeral=True),
    CommandSpec("create", "Cria um comando personalizado", "Comandos personalizados", admin_only=True, ephemeral=True),
    CommandSpec("delete", "Apaga um comando personalizado", "Comandos personalizados", admin_only=True, usage="/delete nome", ephemeral=True),
    CommandSpec("instance", "Envia um GIF de bom dia abencoado", "GIFs"),
    CommandSpec("duvida", "Envia um GIF de duvida", "GIFs"),
    CommandSpec("add", "Adiciona GIF respondendo a uma animacao", "GIFs", admin_only=True, usage="/add instance|duvida", ephemeral=True),
    CommandSpec("removegif", "Remove GIF respondendo a uma animacao", "GIFs", admin_only=True, ephemeral=True),
    CommandSpec("gifstats", "Mostra estatisticas das bases de GIFs", "GIFs", ephemeral=True),
    CommandSpec("backlog", "Lista, cria e gerencia sugestoes", "Backlog", usage="/backlog texto|done|merda|lixeira|limpar", ephemeral=True),
    CommandSpec("sync", "Sincroniza o autocomplete do Telegram", "Administracao", admin_only=True, ephemeral=True),
    CommandSpec("cancelar", "Cancela criacao/edicao em andamento", "Administracao", admin_only=True, autocomplete=False, ephemeral=True),
    CommandSpec("id", "Mostra o ID deste chat", "Utilidades", ephemeral=True),
)


def command_names(commands: Iterable[CommandSpec]) -> list[str]:
    names: list[str] = []
    for command in commands:
        names.append(command.name)
        names.extend(command.aliases)
    return sorted(set(names))


def autocomplete_commands(commands: Iterable[CommandSpec], limit: int = 30) -> list[CommandSpec]:
    return [command for command in commands if command.autocomplete][:limit]


def grouped_commands(commands: Iterable[CommandSpec]) -> dict[str, list[CommandSpec]]:
    grouped: dict[str, list[CommandSpec]] = {}
    for command in commands:
        grouped.setdefault(command.category, []).append(command)
    return grouped


def export_catalog(bot_name: str, commands: Iterable[CommandSpec], custom_commands: dict | None = None) -> dict:
    return {
        "bot": bot_name,
        "commands": [command.to_dict() for command in commands if command.mini_app],
        "customCommands": custom_commands or {},
    }
