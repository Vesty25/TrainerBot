import os
import libsql_experimental as libsql

# Настройки Turso
TURSO_URL = os.getenv(libsql://trainer-pm-bot-vesty25.aws-us-east-2.turso.io, "")
TURSO_TOKEN = os.getenv(eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODYxMjY0NzMsImlkIjoiMDE5ZmRkNmUtOGIwMS03ODNjLWFjYzMtMzg1YjJlMGNlNDM5Iiwia2lkIjoiWlNUNGZDUGx1cFdObWg0RGV2SHBjV1RxTWlMRjNFVUJDQXBxODAyMVJBYyIsInJpZCI6IjQyYWZmMTkzLTY4ZGYtNDQ4YS04YzIwLTI0OWUzZDkzMTA5MSJ9.h033_y68wu9M7DeK666J4NxQenl_A5qdfXP8ckgV5CJYURU_w4waBKAyDyMEzHHpZiufq6m_bOcZkUC00eHmAA, "")

# Если переменные Turso заданы — используем облачную БД
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

if USE_TURSO:
    client = libsql.connect(
        database=TURSO_URL,
        auth_token=TURSO_TOKEN,
    )
else:
    import sqlite3
    DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def get_turso():
    """Возвращает клиент Turso."""
    return client


def get_sqlite_conn():
    """Возвращает соединение SQLite."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def execute_sql(query: str, params: tuple = ()):
    """Выполняет SQL-запрос (INSERT, UPDATE, DELETE, CREATE)."""
    if USE_TURSO:
        return client.execute(query, params)
    else:
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        result = cursor.execute(query, params)
        conn.commit()
        conn.close()
        return result


def fetch_one(query: str, params: tuple = ()):
    """Возвращает одну строку как словарь."""
    if USE_TURSO:
        result = client.execute(query, params)
        rows = result.fetchall()
        if rows:
            row = rows[0]
            return {col: row[i] for i, col in enumerate(result.columns)}
        return None
    else:
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()
        return row


def fetch_all(query: str, params: tuple = ()):
    """Возвращает все строки."""
    if USE_TURSO:
        result = client.execute(query, params)
        rows = result.fetchall()
        return [{col: row[i] for i, col in enumerate(result.columns)} for row in rows]
    else:
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return rows


def init_db():
    """Создаёт таблицы, если их нет."""
    execute_sql("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_test_user INTEGER DEFAULT 0,
            has_played INTEGER DEFAULT 0,
            games_this_month INTEGER DEFAULT 0,
            subscription_expires TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            last_activity TEXT
        )
    """)

    execute_sql("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            payment_method TEXT,
            payment_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    execute_sql("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    execute_sql("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('subscription_enabled', '1')
    """)
