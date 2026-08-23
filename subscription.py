from datetime import datetime, timedelta
from database import fetch_one, execute_sql, fetch_all


def is_test_user(user_id: int) -> bool:
    """Проверяет, является ли пользователь тестовым."""
    row = fetch_one("SELECT is_test_user FROM users WHERE user_id = ?", (user_id,))
    return row is not None and row["is_test_user"] == 1


def is_subscription_enabled() -> bool:
    """Проверяет, включён ли режим подписки глобально."""
    row = fetch_one("SELECT value FROM settings WHERE key = 'subscription_enabled'")
    return row["value"] == "1" if row else True


def is_premium(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная премиум-подписка."""
    if not is_subscription_enabled():
        return True
    if is_test_user(user_id):
        return True

    row = fetch_one(
        "SELECT subscription_expires FROM users WHERE user_id = ?",
        (user_id,)
    )

    if row and row["subscription_expires"]:
        expires = datetime.fromisoformat(row["subscription_expires"])
        return expires > datetime.now()
    return False


def add_subscription(user_id: int, days: int):
    """Добавляет или продлевает подписку на указанное количество дней."""
    row = fetch_one(
        "SELECT subscription_expires FROM users WHERE user_id = ?",
        (user_id,)
    )

    now = datetime.now()
    if row and row["subscription_expires"]:
        current_expires = datetime.fromisoformat(row["subscription_expires"])
        if current_expires > now:
            new_expires = current_expires + timedelta(days=days)
        else:
            new_expires = now + timedelta(days=days)
    else:
        new_expires = now + timedelta(days=days)

    execute_sql("""
        INSERT INTO users (user_id, subscription_expires)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET subscription_expires = ?
    """, (user_id, new_expires.isoformat(), new_expires.isoformat()))


def add_test_user(user_id: int):
    """Добавляет пользователя в список тестовых (вечная подписка)."""
    execute_sql("""
        INSERT INTO users (user_id, is_test_user)
        VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET is_test_user = 1
    """, (user_id,))


def ensure_user_exists(user_id: int, username: str = None) -> bool:
    """Создаёт запись о пользователе, если её нет. Возвращает True, если пользователь новый."""
    row = fetch_one("SELECT user_id, has_played FROM users WHERE user_id = ?", (user_id,))

    if row:
        if username:
            execute_sql(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (username, user_id)
            )
        return row["has_played"] == 0
    else:
        execute_sql(
            "INSERT INTO users (user_id, username, has_played, games_this_month) VALUES (?, ?, 0, 0)",
            (user_id, username)
        )
        return True


def update_activity(user_id: int):
    """Обновляет время последней активности пользователя."""
    execute_sql(
        "UPDATE users SET last_activity = datetime('now') WHERE user_id = ?",
        (user_id,)
    )


def get_games_this_month(user_id: int) -> int:
    """Возвращает количество игр пользователя в текущем месяце."""
    row = fetch_one(
        "SELECT games_this_month FROM users WHERE user_id = ?",
        (user_id,)
    )
    return row["games_this_month"] if row else 0


def increment_games_this_month(user_id: int):
    """Увеличивает счётчик игр в текущем месяце."""
    execute_sql(
        "UPDATE users SET games_this_month = games_this_month + 1 WHERE user_id = ?",
        (user_id,)
    )


def can_play(user_id: int, max_games: int = 5) -> bool:
    """Проверяет, может ли пользователь играть (не превышен лимит)."""
    if is_premium(user_id):
        return True
    games = get_games_this_month(user_id)
    return games < max_games


def get_available_roles(user_id: int, all_roles: dict) -> dict:
    if is_premium(user_id):
        return all_roles
    return {k: v for k, v in all_roles.items() if not v.get("premium", False)}


def get_available_scenarios(user_id: int, all_scenarios: dict) -> dict:
    if is_premium(user_id):
        return all_scenarios
    return {k: v for k, v in all_scenarios.items() if not v.get("premium", False)}


def get_available_goals(user_id: int, all_goals: dict) -> dict:
    if is_premium(user_id):
        return all_goals
    return {k: v for k, v in all_goals.items() if not v.get("premium", False)}


def is_first_game(user_id: int) -> bool:
    """Проверяет, первая ли это игра у пользователя."""
    row = fetch_one("SELECT has_played FROM users WHERE user_id = ?", (user_id,))
    return row is not None and row["has_played"] == 0


def get_all_users_data() -> list:
    """Возвращает все данные о пользователях (для бекапа)."""
    return fetch_all("SELECT * FROM users")


def export_users_to_sql() -> str:
    """Экспортирует данные пользователей в SQL-формате."""
    users = fetch_all("SELECT * FROM users")
    lines = []
    for user in users:
        cols = ", ".join(user.keys())
        vals = ", ".join([f"'{v}'" if v is not None else "NULL" for v in user.values()])
        lines.append(f"INSERT OR REPLACE INTO users ({cols}) VALUES ({vals});")

    settings = fetch_all("SELECT * FROM settings")
    for s in settings:
        cols = ", ".join(s.keys())
        vals = ", ".join([f"'{v}'" if v is not None else "NULL" for v in s.values()])
        lines.append(f"INSERT OR REPLACE INTO settings ({cols}) VALUES ({vals});")

    return "\n".join(lines)

def get_available_groups(user_id: int, all_groups: dict) -> dict:
    """
    Возвращает группы навыков, доступные пользователю.
    Для premium — все 5 групп.
    Для бесплатных — упрощённый набор без группировки (возвращает пустой словарь,
    чтобы bot.py использовал прямые сценарии).
    """
    if is_premium(user_id):
        return all_groups
    return {}  # бесплатные не выбирают группы — им сразу показываются сценарии
