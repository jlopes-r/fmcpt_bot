"""Infraestrutura SQLite e funcoes legadas do bot.

As operacoes de escrita usam ``BEGIN IMMEDIATE`` para serializar a pequena
janela read-modify-write. Isso evita contagens perdidas quando mais de uma
mensagem e processada ao mesmo tempo, sem manter conexoes globais entre
threads.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = str(BASE_DIR / "data" / "bocadeleite.db")
BUSY_TIMEOUT_MS = max(1_000, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "10000")))


def _resolved_path(db_path: str | os.PathLike[str] | None = None) -> Path:
    return Path(db_path if db_path is not None else DB_PATH).resolve()


@contextmanager
def connection(
    db_path: str | os.PathLike[str] | None = None,
    *,
    write: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Abre uma conexao curta, configurada e sempre fechada.

    ``write=True`` inicia uma transacao IMMEDIATE. O lock e obtido antes de
    qualquer leitura que participe da escrita, tornando operacoes compostas
    atomicas entre processos e threads.
    """

    path = _resolved_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=BUSY_TIMEOUT_MS / 1_000,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except BaseException:
        if write and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_legacy_links(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(links)")}
    if columns and "chat_id" not in columns:
        conn.execute("ALTER TABLE links RENAME TO links_old")
        conn.execute(
            """
            CREATE TABLE links (
                url_norm TEXT NOT NULL,
                chat_id INTEGER NOT NULL DEFAULT 0,
                first_user TEXT,
                first_user_id INTEGER,
                count INTEGER NOT NULL DEFAULT 1,
                timestamp REAL NOT NULL,
                PRIMARY KEY (url_norm, chat_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO links
                (url_norm, chat_id, first_user, first_user_id, count, timestamp)
            SELECT url_norm, 0, first_user, first_user_id, count, timestamp
            FROM links_old
            """
        )
        conn.execute("DROP TABLE links_old")


def init_db(db_path: str | os.PathLike[str] | None = None) -> None:
    """Cria/migra todo o schema de forma idempotente."""

    with connection(db_path, write=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                url_norm TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                first_user TEXT,
                first_user_id INTEGER,
                count INTEGER NOT NULL DEFAULT 1,
                timestamp REAL NOT NULL,
                PRIMARY KEY (url_norm, chat_id)
            )
            """
        )
        _migrate_legacy_links(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS links_diarios (
                url_norm TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                dia TEXT NOT NULL,
                first_user TEXT,
                first_user_id INTEGER,
                count INTEGER NOT NULL DEFAULT 1,
                timestamp REAL NOT NULL,
                PRIMARY KEY (url_norm, chat_id, dia)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vacilos (
                user_id INTEGER NOT NULL,
                user_name TEXT,
                timestamp REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campeoes (
                mes_ano TEXT PRIMARY KEY,
                user_name TEXT,
                total_vacilos INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                url_norm TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'downloading', 'uploading', 'completed', 'failed')
                ),
                priority INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                error_type TEXT,
                error_message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                cost INTEGER NOT NULL DEFAULT 1 CHECK (cost > 0),
                occurred_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_events (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('counter', 'gauge', 'timing')),
                value REAL NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT '',
                account TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                status_code INTEGER,
                job_id TEXT,
                labels_json TEXT NOT NULL DEFAULT '{}',
                timestamp REAL NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE SET NULL
            )
            """
        )

        indexes = (
            "CREATE INDEX IF NOT EXISTS idx_links_timestamp ON links(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_links_user ON links(first_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_links_diarios_timestamp ON links_diarios(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_vacilos_timestamp ON vacilos(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_vacilos_user_timestamp ON vacilos(user_id, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_campeoes_rank ON campeoes(total_vacilos DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_timestamp ON jobs(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_user_timestamp ON jobs(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_queue_rank ON jobs(status, priority DESC, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_chat_url ON jobs(chat_id, url_norm, created_at DESC)",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_active_chat_url
               ON jobs(idempotency_key)
               WHERE status IN ('queued', 'downloading', 'uploading')""",
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_window ON rate_limit_events(scope, subject_key, occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metric_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_metrics_platform ON metric_events(name, platform, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_metrics_job ON metric_events(job_id)",
        )
        for statement in indexes:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 2")


def _dia_atual() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def checar_link(url_norm: str, chat_id: int = 0) -> tuple[bool, dict[str, object]]:
    """Verifica se o link ja foi enviado hoje no chat especifico."""

    with connection() as conn:
        row = conn.execute(
            """
            SELECT count, first_user, first_user_id
            FROM links_diarios
            WHERE url_norm = ? AND chat_id = ? AND dia = ?
            """,
            (url_norm, chat_id, _dia_atual()),
        ).fetchone()
    if row:
        return True, {
            "primeiro_user": row["first_user"],
            "primeiro_id": row["first_user_id"],
            "vezes": row["count"],
        }
    return False, {}


def registrar_link_e_checar(
    url_norm: str,
    chat_id: int,
    user_name: str,
    user_id: int,
) -> tuple[bool, dict[str, object]]:
    agora = datetime.now()
    agora_ts = agora.timestamp()
    dia = agora.strftime("%Y-%m-%d")
    duplicado_hoje = False
    info: dict[str, object] = {}

    with connection(write=True) as conn:
        global_row = conn.execute(
            "SELECT count, first_user_id FROM links WHERE url_norm = ? AND chat_id = ?",
            (url_norm, chat_id),
        ).fetchone()
        daily_row = conn.execute(
            """
            SELECT count, first_user, first_user_id
            FROM links_diarios
            WHERE url_norm = ? AND chat_id = ? AND dia = ?
            """,
            (url_norm, chat_id, dia),
        ).fetchone()

        if global_row:
            conn.execute(
                "UPDATE links SET count = count + 1 WHERE url_norm = ? AND chat_id = ?",
                (url_norm, chat_id),
            )
            if global_row["first_user_id"] != user_id:
                conn.execute(
                    "INSERT INTO vacilos (user_id, user_name, timestamp) VALUES (?, ?, ?)",
                    (user_id, user_name, agora_ts),
                )
        else:
            conn.execute(
                """
                INSERT INTO links
                    (url_norm, chat_id, first_user, first_user_id, count, timestamp)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (url_norm, chat_id, user_name, user_id, agora_ts),
            )

        if daily_row:
            novo_count = int(daily_row["count"]) + 1
            conn.execute(
                """
                UPDATE links_diarios SET count = ?
                WHERE url_norm = ? AND chat_id = ? AND dia = ?
                """,
                (novo_count, url_norm, chat_id, dia),
            )
            duplicado_hoje = daily_row["first_user_id"] != user_id
            if duplicado_hoje:
                info = {
                    "primeiro_user": daily_row["first_user"],
                    "primeiro_id": daily_row["first_user_id"],
                    "vezes": novo_count,
                }
        else:
            conn.execute(
                """
                INSERT INTO links_diarios
                    (url_norm, chat_id, dia, first_user, first_user_id, count, timestamp)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (url_norm, chat_id, dia, user_name, user_id, agora_ts),
            )

    return duplicado_hoje, info


def registrar_vacilo_manual(user_id: int, user_name: str) -> None:
    """Registra um vacilo manualmente via /repetido."""

    with connection(write=True) as conn:
        conn.execute(
            "INSERT INTO vacilos (user_id, user_name, timestamp) VALUES (?, ?, ?)",
            (user_id, user_name, datetime.now().timestamp()),
        )


def get_ranking_semanal() -> list[tuple[str, int]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT user_name, COUNT(*) AS total
            FROM vacilos
            WHERE timestamp > ?
            GROUP BY user_id
            ORDER BY total DESC
            LIMIT 10
            """,
            ((datetime.now() - timedelta(days=7)).timestamp(),),
        ).fetchall()
    return [(str(row["user_name"]), int(row["total"])) for row in rows]


def get_lider_mes_atual() -> list[tuple[str, int]]:
    agora = datetime.now()
    inicio_mes = datetime(agora.year, agora.month, 1).timestamp()
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT user_name, COUNT(*) AS total
            FROM vacilos
            WHERE timestamp >= ?
            GROUP BY user_id
            ORDER BY total DESC
            """,
            (inicio_mes,),
        ).fetchall()
    return [(str(row["user_name"]), int(row["total"])) for row in rows]


def fechar_mes_passado_se_preciso() -> tuple[str | None, str | None]:
    agora = datetime.now()
    primeiro_dia_atual = datetime(agora.year, agora.month, 1)
    mes_passado = primeiro_dia_atual - timedelta(days=1)
    chave = mes_passado.strftime("%Y-%m")
    with connection(write=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM campeoes WHERE mes_ano = ?", (chave,)
        ).fetchone()
        if exists:
            return None, None
        inicio = datetime(mes_passado.year, mes_passado.month, 1).timestamp()
        vencedor = conn.execute(
            """
            SELECT user_name, COUNT(*) AS total
            FROM vacilos
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY user_id
            ORDER BY total DESC
            LIMIT 1
            """,
            (inicio, primeiro_dia_atual.timestamp()),
        ).fetchone()
        if not vencedor:
            return None, None
        conn.execute(
            "INSERT INTO campeoes (mes_ano, user_name, total_vacilos) VALUES (?, ?, ?)",
            (chave, vencedor["user_name"], vencedor["total"]),
        )
        return str(vencedor["user_name"]), chave


def get_hall_da_fama_ano() -> list[tuple[str, int]]:
    ano = str(datetime.now().year)
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT user_name, COUNT(*) AS vits
            FROM campeoes
            WHERE mes_ano LIKE ?
            GROUP BY user_name
            ORDER BY vits DESC
            """,
            (f"{ano}%",),
        ).fetchall()
    return [(str(row["user_name"]), int(row["vits"])) for row in rows]


__all__ = [
    "BUSY_TIMEOUT_MS",
    "DB_PATH",
    "checar_link",
    "connection",
    "fechar_mes_passado_se_preciso",
    "get_hall_da_fama_ano",
    "get_lider_mes_atual",
    "get_ranking_semanal",
    "init_db",
    "registrar_link_e_checar",
    "registrar_vacilo_manual",
]
