import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")


def get_conn():
    """Возвращает соединение с БД (PostgreSQL или SQLite)."""
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn
    else:
        import sqlite3
        DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def execute_sql(query: str, params: tuple = ()):
    """Выполняет SQL-запрос (INSERT, UPDATE, DELETE, CREATE)."""
    conn = get_conn()
    cursor = conn.cursor()

    # Адаптация синтаксиса SQLite → PostgreSQL
    if DATABASE_URL:
        query = query.replace("AUTOINCREMENT", "")
        query = query.replace("datetime('now')", "NOW()")
        query = query.replace("INSERT OR IGNORE", "INSERT ON CONFLICT DO NOTHING")
        query = query.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")

    cursor.execute(query, params)
    if not DATABASE_URL:
        conn.commit()
    conn.close()


def fetch_one(query: str, params: tuple = ()):
    """Возвращает одну строку как словарь."""
    conn = get_conn()
    cursor = conn.cursor()

    if DATABASE_URL:
        query = query.replace("datetime('now')", "NOW()")

    cursor.execute(query, params)
    row = cursor.fetchone()
    
    if row:
        cols = [desc[0] for desc in cursor.description]
        result = dict(zip(cols, row))
    else:
        result = None
    
    conn.close()
    return result


def fetch_all(query: str, params: tuple = ()):
    """Возвращает все строки."""
    conn = get_conn()
    cursor = conn.cursor()

    if DATABASE_URL:
        query = query.replace("datetime('now')", "NOW()")

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    
    result = [dict(zip(cols, row)) for row in rows]
    conn.close()
    return result


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

    # В PostgreSQL другой синтаксис для INSERT OR IGNORE
    if DATABASE_URL:
        execute_sql("""
            INSERT INTO settings (key, value) 
            VALUES ('subscription_enabled', '1')
            ON CONFLICT (key) DO NOTHING
        """)
    else:
        execute_sql("""
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('subscription_enabled', '1')
        """)
