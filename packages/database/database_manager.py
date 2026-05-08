import sqlite3
import os
from datetime import datetime, timedelta

# Localiza a raiz do projeto (bot/) subindo dois níveis
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
DB_PATH = os.path.join(BASE_DIR, "data", "bocadeleite.db")

def init_db():
    """Garante que a pasta data existe e cria o banco."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tabela links agora com first_user_id para menções futuras
    cursor.execute('''CREATE TABLE IF NOT EXISTS links 
                      (url_norm TEXT PRIMARY KEY, first_user TEXT, first_user_id INTEGER, count INTEGER, timestamp REAL)''')
    
    # Migração simples: se a coluna first_user_id não existir, adiciona
    try:
        cursor.execute("ALTER TABLE links ADD COLUMN first_user_id INTEGER")
    except:
        pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS vacilos 
                      (user_id INTEGER, user_name TEXT, timestamp REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS campeoes 
                      (mes_ano TEXT PRIMARY KEY, user_name TEXT, total_vacilos INTEGER)''')
    conn.commit()
    conn.close()

def checar_link(url_norm):
    """Apenas verifica se o link já existe no banco, sem registrar."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT count, first_user, first_user_id FROM links WHERE url_norm = ?", (url_norm,))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        return True, {"primeiro_user": res[1], "primeiro_id": res[2], "vezes": res[0]}
    return False, {}

def registrar_link_e_checar(url_norm, user_name, user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT count, first_user, first_user_id FROM links WHERE url_norm = ?", (url_norm,))
    res = cursor.fetchone()
    
    duplicado = False
    info = {}

    if res:
        duplicado = True
        novo_count = res[0] + 1
        cursor.execute("UPDATE links SET count = ? WHERE url_norm = ?", (novo_count, url_norm))
        cursor.execute("INSERT INTO vacilos VALUES (?, ?, ?)", (user_id, user_name, datetime.now().timestamp()))
        info = {"primeiro_user": res[1], "primeiro_id": res[2], "vezes": novo_count}
    else:
        cursor.execute("INSERT INTO links (url_norm, first_user, first_user_id, count, timestamp) VALUES (?, ?, ?, 1, ?)", 
                       (url_norm, user_name, user_id, datetime.now().timestamp()))
    
    conn.commit()
    conn.close()
    return duplicado, info

def get_ranking_semanal():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    sete_dias = (datetime.now() - timedelta(days=7)).timestamp()
    cursor.execute("SELECT user_name, COUNT(*) as total FROM vacilos WHERE timestamp > ? GROUP BY user_id ORDER BY total DESC LIMIT 10", (sete_dias,))
    res = cursor.fetchall()
    conn.close()
    return res

def get_lider_mes_atual():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    agora = datetime.now()
    inicio_mes = datetime(agora.year, agora.month, 1).timestamp()
    cursor.execute("SELECT user_name, COUNT(*) as total FROM vacilos WHERE timestamp >= ? GROUP BY user_id ORDER BY total DESC", (inicio_mes,))
    res = cursor.fetchall()
    conn.close()
    return res

def fechar_mes_passado_se_preciso():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    agora = datetime.now()
    p_dia_atual = datetime(agora.year, agora.month, 1)
    mes_p = p_dia_atual - timedelta(days=1)
    chave = mes_p.strftime("%Y-%m")
    cursor.execute("SELECT mes_ano FROM campeoes WHERE mes_ano = ?", (chave,))
    if not cursor.fetchone():
        inicio_p = datetime(mes_p.year, mes_p.month, 1).timestamp()
        fim_p = p_dia_atual.timestamp()
        cursor.execute("SELECT user_name, COUNT(*) as total FROM vacilos WHERE timestamp >= ? AND timestamp < ? GROUP BY user_id ORDER BY total DESC LIMIT 1", (inicio_p, fim_p))
        v = cursor.fetchone()
        if v:
            cursor.execute("INSERT INTO campeoes VALUES (?, ?, ?)", (chave, v[0], v[1]))
            conn.commit()
            conn.close()
            return v[0], chave
    conn.close()
    return None, None

def get_hall_da_fama_ano():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    ano = str(datetime.now().year)
    cursor.execute("SELECT user_name, COUNT(*) as vits FROM campeoes WHERE mes_ano LIKE ? GROUP BY user_name ORDER BY vits DESC", (f"{ano}%",))
    res = cursor.fetchall()
    conn.close()
    return res