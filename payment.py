"""
Модуль интеграции с ЮKassa.
Документация: https://yookassa.ru/developers
"""

import uuid
from datetime import datetime, timedelta
import yookassa
from yookassa import Payment, Refund

import config
from subscription import add_subscription


# Настройка ЮKassa
if config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY:
    yookassa.Configuration.configure(
        account_id=config.YOOKASSA_SHOP_ID,
        secret_key=config.YOOKASSA_SECRET_KEY,
    )


# Стоимость подписки
SUBSCRIPTION_PRICE = 299.00  # ₽
SUBSCRIPTION_DURATION_DAYS = 30


def is_yookassa_configured() -> bool:
    """Проверяет, настроена ли ЮKassa."""
    return bool(config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY)


async def create_subscription_payment(user_id: int, user_email: str = None) -> dict:
    # ... (проверка is_yookassa_configured) ...

    # Нужен email покупателя — можно запросить у пользователя в боте
    # Если email нет, ЮKassa вернёт ошибку
    customer_email = user_email or f"user_{user_id}@example.com"  # заглушка, лучше собирать реальный email

    payment = Payment.create({
        "amount": {
            "value": f"{SUBSCRIPTION_PRICE:.2f}",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me/Trainer_PM_bot",
        },
        "capture": True,
        "description": f"Премиум-подписка на 30 дней (user_id: {user_id})",
        "metadata": {
            "user_id": str(user_id),
            "payment_type": "subscription",
            "duration_days": SUBSCRIPTION_DURATION_DAYS,
        },
        # НОВОЕ: объект receipt для 54-ФЗ
         "receipt": {
            "customer": {"email": user_email},
            "items": [{
                "description": "Премиум-подписка на 30 дней",
                "quantity": 1,
                "amount": {"value": f"{SUBSCRIPTION_PRICE:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_subject": "service",
                "payment_mode": "full_payment",
            }],
        },
    }, uuid.uuid4())

    # Сохраняем платёж в БД
    from database import execute_sql
    execute_sql(
        """INSERT INTO payments (user_id, amount, currency, payment_method, payment_id, status)
           VALUES (?, ?, ?, 'yookassa', ?, 'pending')""",
        (user_id, SUBSCRIPTION_PRICE, "RUB", payment.id)
    )

    return {
        "success": True,
        "payment_id": payment.id,
        "confirmation_url": payment.confirmation.confirmation_url,
        "amount": SUBSCRIPTION_PRICE,
    }


async def check_payment_status(payment_id: str) -> str:
    """
    Проверяет статус платежа.
    Возвращает: 'pending', 'succeeded', 'canceled', 'error'
    """
    if not is_yookassa_configured():
        return "error"

    try:
        payment = Payment.find_one(payment_id)
        return payment.status
    except Exception as e:
        return "error"


async def process_payment_callback(data: dict) -> dict:
    """
    Обрабатывает webhook от ЮKassa об изменении статуса платежа.
    Вызывается при получении уведомления.
    """
    if not is_yookassa_configured():
        return {"success": False, "error": "ЮKassa не настроена"}

    payment_id = data.get("object", {}).get("id")
    status = data.get("object", {}).get("status")
    metadata = data.get("object", {}).get("metadata", {})

    user_id = int(metadata.get("user_id", 0))
    payment_type = metadata.get("payment_type", "")

    if status == "succeeded" and payment_type == "subscription" and user_id > 0:
        # Активируем подписку
        add_subscription(user_id, SUBSCRIPTION_DURATION_DAYS)

        # Обновляем статус в БД
        from database import execute_sql
        execute_sql(
            "UPDATE payments SET status = 'paid' WHERE payment_id = ?",
            (payment_id,)
        )

        return {"success": True, "user_id": user_id}

    if status == "canceled":
        from database import execute_sql
        execute_sql(
            "UPDATE payments SET status = 'canceled' WHERE payment_id = ?",
            (payment_id,)
        )

    return {"success": False, "status": status}


async def get_payment_info(payment_id: str) -> dict:
    """
    Возвращает информацию о платеже.
    """
    if not is_yookassa_configured():
        return {"success": False, "error": "ЮKassa не настроена"}

    try:
        payment = Payment.find_one(payment_id)
        return {
            "success": True,
            "status": payment.status,
            "amount": float(payment.amount.value),
            "currency": payment.amount.currency,
            "created_at": payment.created_at,
            "description": payment.description,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_subscription_price() -> float:
    """Возвращает стоимость подписки."""
    return SUBSCRIPTION_PRICE


def get_subscription_duration() -> int:
    """Возвращает длительность подписки в днях."""
    return SUBSCRIPTION_DURATION_DAYS

