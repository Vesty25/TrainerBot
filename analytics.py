"""
Аналитика: запись событий и сбор исторической статистики.
"""

from datetime import datetime, timedelta
from database import execute_sql, fetch_one, fetch_all, DATABASE_URL


# === Запись событий ===

def log_event(user_id: int, event_type: str, event_data: str = None):
    """
    Записывает событие в журнал.
    Типы: 'registration', 'game_start', 'game_finish', 'payment',
          'subscription_add', 'subscription_remove'
    """
    execute_sql(
        "INSERT INTO events (user_id, event_type, event_data) VALUES (?, ?, ?)",
        (user_id, event_type, event_data)
    )


# === Хелпер для сравнения дат ===

def _date_condition(days: int = None) -> str:
    """Возвращает SQL-условие для фильтра по дате."""
    if DATABASE_URL:
        # PostgreSQL
        if days is None:
            return "1=1"
        return f"created_at::timestamp > NOW() - INTERVAL '{days} days'"
    else:
        # SQLite
        if days is None:
            return "1=1"
        return f"created_at > datetime('now', '-{days} days')"


# === Статистика за период ===

def get_stats_all() -> dict:
    """Статистика с момента запуска бота (все события)."""
    return _get_stats(days=None)


def get_stats_period(days: int) -> dict:
    """Статистика за последние N дней."""
    return _get_stats(days=days)


def _get_stats(days: int = None) -> dict:
    """Общая функция сбора статистики."""
    condition = _date_condition(days)

    # Регистрации
    reg_row = fetch_one(
        f"SELECT COUNT(*) as c FROM events WHERE event_type = 'registration' AND {condition}"
    )
    registrations = reg_row["c"] if reg_row else 0

    # Запуски игр
    game_row = fetch_one(
        f"SELECT COUNT(*) as c FROM events WHERE event_type = 'game_start' AND {condition}"
    )
    games = game_row["c"] if game_row else 0

    # Завершённые игры (с анализом)
    finish_row = fetch_one(
        f"SELECT COUNT(*) as c FROM events WHERE event_type = 'game_finish' AND {condition}"
    )
    finished = finish_row["c"] if finish_row else 0

    # Платежи
    pay_row = fetch_one(
        f"SELECT COUNT(*) as c, COALESCE(SUM(CAST(event_data AS REAL)), 0) as total "
        f"FROM events WHERE event_type = 'payment' AND {condition}"
    )
    if pay_row:
        payments_count = pay_row["c"] or 0
        # Для PostgreSQL SUM возвращает Decimal, приводим к float
        try:
            payments_total = float(pay_row["total"]) if pay_row["total"] else 0.0
        except (TypeError, ValueError):
            payments_total = 0.0
    else:
        payments_count = 0
        payments_total = 0.0

    # Уникальные пользователи, которые играли
    unique_players_row = fetch_one(
        f"SELECT COUNT(DISTINCT user_id) as c FROM events "
        f"WHERE event_type = 'game_start' AND {condition}"
    )
    unique_players = unique_players_row["c"] if unique_players_row else 0

    # Средняя длина диалога
    # Считаем по game_finish: сколько реплик было в каждой завершённой игре
    # (если ты пишешь это в event_data)
    avg_length_row = fetch_one(
        f"SELECT AVG(CAST(event_data AS REAL)) as avg_len FROM events "
        f"WHERE event_type = 'game_finish' AND event_data IS NOT NULL AND {condition}"
    )
    try:
        avg_length = float(avg_length_row["avg_len"]) if avg_length_row and avg_length_row["avg_len"] else 0.0
    except (TypeError, ValueError):
        avg_length = 0.0

    return {
        "registrations": registrations,
        "unique_players": unique_players,
        "games_started": games,
        "games_finished": finished,
        "payments_count": payments_count,
        "payments_total": payments_total,
        "avg_dialog_length": round(avg_length, 1),
    }


def get_users_stats(days: int = None) -> dict:
    """Статистика по пользователям (всего / premium / бесплатные)."""
    condition = _date_condition(days)

    total_row = fetch_one(f"SELECT COUNT(*) as c FROM users WHERE {condition}")
    total = total_row["c"] if total_row else 0

    if DATABASE_URL:
        prem_condition = (
            f"({condition}) AND "
            f"(subscription_expires::timestamp > NOW() OR is_test_user = 1)"
        )
    else:
        prem_condition = (
            f"({condition}) AND "
            f"(subscription_expires > datetime('now') OR is_test_user = 1)"
        )

    premium_row = fetch_one(f"SELECT COUNT(*) as c FROM users WHERE {prem_condition}")
    premium = premium_row["c"] if premium_row else 0

    test_row = fetch_one(f"SELECT COUNT(*) as c FROM users WHERE ({condition}) AND is_test_user = 1")
    test = test_row["c"] if test_row else 0

    played_row = fetch_one(f"SELECT COUNT(*) as c FROM users WHERE ({condition}) AND has_played = 1")
    played = played_row["c"] if played_row else 0

    return {
        "total": total,
        "premium": premium,
        "test": test,
        "played": played,
        "free": total - premium,
    }
