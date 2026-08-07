import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
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

    cursor.execute("""
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO settings (key, value) VALUES ('subscription_enabled', '1')
    """)

    conn.commit()
    conn.close()


def reset_monthly_games():
    """Сбрасывает счётчик игр в начале нового месяца."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET games_this_month = 0")
    conn.commit()
    conn.close()
