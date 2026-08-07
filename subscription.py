from datetime import datetime, timedelta
from database import get_connection


def is_test_user(user_id: int) -> bool:
    """Проверяет, является ли пользователь тестовым."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_test_user FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None and row["is_test_user"] == 1


def is_subscription_enabled() -> bool:
    """Проверяет, включён ли режим подписки глобально."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'subscription_enabled'")
    row = cursor.fetchone()
    conn.close()
    return row["value"] == "1" if row else True


def is_premium(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная премиум-подписка."""
    # Если подписка выключена глобально — все premium
    if not is_subscription_enabled():
        return True

    # Тестовые пользователи — вечная подписка
    if is_test_user(user_id):
        return True

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT subscription_expires FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row and row["subscription_expires"]:
        expires = datetime.fromisoformat(row["subscription_expires"])
        return expires > datetime.now()
    return False


def add_subscription(user_id: int, days: int):
    """Добавляет или продлевает подписку на указанное количество дней."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT subscription_expires FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()

    now = datetime.now()
    if row and row["subscription_expires"]:
        current_expires = datetime.fromisoformat(row["subscription_expires"])
        if current_expires > now:
            new_expires = current_expires + timedelta(days=days)
        else:
            new_expires = now + timedelta(days=days)
    else:
        new_expires = now + timedelta(days=days)

    cursor.execute("""
        INSERT INTO users (user_id, subscription_expires)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET subscription_expires = ?
    """, (user_id, new_expires.isoformat(), new_expires.isoformat()))

    conn.commit()
    conn.close()


def add_test_user(user_id: int):
    """Добавляет пользователя в список тестовых (вечная подписка)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, is_test_user)
        VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET is_test_user = 1
    """, (user_id,))
    conn.commit()
    conn.close()


def ensure_user_exists(user_id: int, username: str = None) -> bool:
    """Создаёт запись о пользователе, если её нет. Возвращает True, если пользователь новый."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, has_played FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if row:
        # Пользователь существует — обновляем username
        if username:
            cursor.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (username, user_id)
            )
        conn.commit()
        conn.close()
        return row["has_played"] == 0  # True если ещё не играл (т.е. "новый")
    else:
        # Новый пользователь
        cursor.execute("""
            INSERT INTO users (user_id, username, has_played)
            VALUES (?, ?, 0)
        """, (user_id, username))
        conn.commit()
        conn.close()
        return True  # новый пользователь


def update_activity(user_id: int):
    """Обновляет время последней активности пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_activity = datetime('now') WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def get_available_roles(user_id: int, all_roles: dict) -> dict:
    """Возвращает роли, доступные пользователю."""
    if is_premium(user_id):
        return all_roles
    return {k: v for k, v in all_roles.items() if not v.get("premium", False)}


def get_available_scenarios(user_id: int, all_scenarios: dict) -> dict:
    """Возвращает сценарии, доступные пользователю."""
    if is_premium(user_id):
        return all_scenarios
    return {k: v for k, v in all_scenarios.items() if not v.get("premium", False)}


def get_available_goals(user_id: int, all_goals: dict) -> dict:
    """Возвращает цели, доступные пользователю."""
    if is_premium(user_id):
        return all_goals
    return {k: v for k, v in all_goals.items() if not v.get("premium", False)}
