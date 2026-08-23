import asyncio
import logging
import random
import os
import tempfile

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, StateFilter
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import web

import config
from states import GameSetup
from roles import ROLES
from scenarios import SCENARIOS
from goals import GOALS
from prompts import build_system_prompt
from deepseek_client import get_ai_response
from database import init_db, execute_sql, fetch_one
from subscription import (
    is_premium, is_test_user, ensure_user_exists,
    update_activity, get_available_roles,
    get_available_scenarios, get_available_goals,
    add_test_user, can_play, increment_games_this_month,
    get_games_this_month, is_first_game,
)


# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Прокси для Telegram API
if config.PROXY_URL:
    session = AiohttpSession(proxy=config.PROXY_URL)
else:
    session = AiohttpSession()

bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())

# Хранилище истории диалогов
user_messages: dict[int, list[dict]] = {}
# Хранилище параметров игры
user_game_data: dict[int, dict] = {}
# Счётчик реплик пользователя в диалоге
user_reply_count: dict[int, int] = {}
# Хранилище выбранных сообщений для удаления: user_id -> [message_id, ...]
user_selection_messages: dict[int, list[int]] = {}

# Сопоставление кнопок и значений
ROLE_BUTTONS = {}
SCENARIO_BUTTONS = {}
GOAL_BUTTONS = {}

# ID админов
ADMIN_USER_IDS = [455307194]  # ← Замени на свой Telegram ID

# Максимальное количество бесплатных игр в месяц
MAX_FREE_GAMES = 5


# === Клавиатуры ===

def get_start_keyboard(user_id: int):
    """Клавиатура с кнопками Старт и Подписка."""
    buttons = [[types.KeyboardButton(text="🚀 Старт")]]
    if not is_premium(user_id):
        buttons.append([types.KeyboardButton(text="💎 Подписка")])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Нажми Старт, чтобы начать",
    )


def get_stop_keyboard():
    """Клавиатура с кнопкой Завершить диалог."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🛑 Завершить диалог")]],
        resize_keyboard=True,
        input_field_placeholder="Нажми, чтобы завершить диалог",
    )


def get_main_menu_keyboard(user_id: int):
    """Клавиатура главного меню (после завершения игры)."""
    buttons = [[types.KeyboardButton(text="🚀 Старт")]]
    if is_premium(user_id):
        buttons.append([types.KeyboardButton(text="🎲 Случайная игра")])
    if not is_premium(user_id):
        buttons.append([types.KeyboardButton(text="💎 Подписка")])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def build_role_keyboard(user_id: int = None):
    """Клавиатура выбора роли + кнопки Случайная игра и Прервать."""
    global ROLE_BUTTONS
    ROLE_BUTTONS = {}
    buttons = []
    for i, role_name in enumerate(ROLES.keys(), start=1):
        button_text = f"Вариант {i}"
        ROLE_BUTTONS[button_text] = role_name
        buttons.append([types.KeyboardButton(text=button_text)])

    # Для premium добавляем Случайную игру
    if user_id and is_premium(user_id):
        buttons.append([types.KeyboardButton(text="🎲 Случайная игра")])

    buttons.append([types.KeyboardButton(text="🛑 Прервать")])

    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def build_scenario_keyboard():
    """Клавиатура выбора сценария + кнопки Назад и Прервать."""
    global SCENARIO_BUTTONS
    SCENARIO_BUTTONS = {}
    buttons = []
    for i, scenario_name in enumerate(SCENARIOS.keys(), start=1):
        button_text = f"Вариант {i}"
        SCENARIO_BUTTONS[button_text] = scenario_name
        buttons.append(button_text)

    rows = []
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        rows.append([types.KeyboardButton(text=text) for text in row])

    rows.append([
        types.KeyboardButton(text="↩️ Назад"),
        types.KeyboardButton(text="🛑 Прервать"),
    ])

    return types.ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def build_goal_keyboard():
    """Клавиатура выбора цели + кнопки Назад и Прервать."""
    global GOAL_BUTTONS
    GOAL_BUTTONS = {}
    buttons = []
    for i, goal_name in enumerate(GOALS.keys(), start=1):
        button_text = f"Вариант {i}"
        GOAL_BUTTONS[button_text] = goal_name
        buttons.append([types.KeyboardButton(text=button_text)])

    buttons.append([
        types.KeyboardButton(text="↩️ Назад"),
        types.KeyboardButton(text="🛑 Прервать"),
    ])

    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


# === Тексты ===

def build_role_text(user_id: int):
    """Текст с описанием ролей."""
    available_roles = get_available_roles(user_id, ROLES)
    all_roles = ROLES
    is_prem = is_premium(user_id)

    text = "🎭 <b>Шаг 1/3: Выбери роль оппонента</b>\n\n"
    text += "👇 <b>Нажми на одну из кнопок ниже:</b>\n\n"

    i = 0
    for role_name, role_data in all_roles.items():
        i += 1
        if role_name in available_roles:
            text += f"<b>Вариант {i}: {role_name}</b>\n{role_data['description']}\n\n"
        else:
            text += f"🔒 <b>Вариант {i}: {role_name}</b>\n"
            text += f"<i>{role_data['description']}</i>\n\n"

    if is_prem:
        text += "🎲 <b>Или нажми «Случайная игра»</b> — бот сам выберет роль, сценарий и цель.\n\n"

    return text


def build_scenario_text(selected_role: str, user_id: int):
    """Текст с описанием сценариев."""
    available_scenarios = get_available_scenarios(user_id, SCENARIOS)
    all_scenarios = SCENARIOS

    text = f"✅ <b>Роль оппонента:</b> {selected_role}\n\n"
    text += "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
    text += "👇 <b>Нажми на одну из кнопок ниже:</b>\n\n"

    i = 0
    for name, data in all_scenarios.items():
        i += 1
        context = data["context"].replace("Контекст: ", "")
        if len(context) > 150:
            context = context[:150].rsplit(".", 1)[0] + "."

        if name in available_scenarios:
            text += f"<b>Вариант {i}: {name}</b>\n{context}\n\n"
        else:
            text += f"🔒 <b>Вариант {i}: {name}</b>\n"
            text += f"<i>{context}</i>\n\n"

    return text


def build_goal_text(selected_scenario: str, user_id: int):
    """Текст с описанием целей."""
    available_goals = get_available_goals(user_id, GOALS)
    all_goals = GOALS

    text = f"✅ <b>Сценарий:</b> {selected_scenario}\n\n"
    text += "🎯 <b>Шаг 3/3: Выбери свою основную цель</b>\n\n"
    text += "👇 <b>Нажми на одну из кнопок ниже:</b>\n\n"

    i = 0
    for goal_name, goal_data in all_goals.items():
        i += 1
        if goal_name in available_goals:
            text += f"<b>Вариант {i}: {goal_name}</b>\n{goal_data['description']}\n\n"
        else:
            text += f"🔒 <b>Вариант {i}: {goal_name}</b>\n"
            text += f"<i>{goal_data['description']}</i>\n\n"

    return text


def get_welcome_text(user_id: int, first_time: bool):
    """Приветственный текст. Разный для новых и вернувшихся."""
    is_prem = is_premium(user_id)
    is_test = is_test_user(user_id)

    if first_time:
        greeting = "👋 <b>Привет!</b> Я — тренажёр деловых переговоров.\n\n"
    else:
        greeting = "👋 <b>С возвращением!</b> Давай сыграем ещё раз.\n\n"

    greeting += "Сейчас мы настроим игру. На каждом шаге просто нажимай на кнопку.\n\n"

    if is_prem:
        if is_test:
            greeting += (
                "🧪 <b>У вас тестовый доступ.</b>\n"
                "Вам доступны все функции премиум-подписки бессрочно.\n\n"
            )
        else:
            greeting += "💎 <b>У вас активна премиум-подписка!</b> Доступны все функции.\n\n"
    else:
        games = get_games_this_month(user_id)
        remaining = MAX_FREE_GAMES - games
        greeting += (
            "💎 <b>Хочешь больше?</b>\n"
            "С премиум-подпиской доступны:\n"
            "• 6 ролей оппонента (сейчас 3)\n"
            "• 20 сценариев (сейчас 10)\n"
            "• 6 целей (сейчас 3)\n"
            "• Случайная игра\n"
            "• Безлимитные диалоги\n"
            "• Расширенный анализ каждого диалога\n\n"
            "Нажми <b>«💎 Подписка»</b> в главном меню.\n\n"
            f"🆓 <b>Бесплатный доступ:</b>\n"
            f"• 3 роли, 10 сценариев, 3 цели\n"
            f"• {remaining} из {MAX_FREE_GAMES} диалогов в этом месяце осталось\n"
            f"• Первый диалог — с расширенным анализом\n\n"
        )

    greeting += build_role_text(user_id)
    return greeting


def get_final_config_text(role_name: str, scenario_name: str, goal_name: str, user_id: int, skill_group: str = None):
    """Финальное сообщение с выбранными параметрами."""
    role_data = ROLES[role_name]
    scenario_data = SCENARIOS[scenario_name]
    goal_data = GOALS[goal_name]
    is_prem = is_premium(user_id)
    first_game = is_first_game(user_id)

    text = "✅ <b>Игра настроена!</b>\n\n"
    text += f"🎭 <b>Роль оппонента:</b> {role_name}\n"
    text += f"<i>{role_data['description']}</i>\n\n"

    # Добавляем навык, если есть
    if scenario_data.get("skill"):
        text += f"🧠 <b>Прокачиваемый навык:</b> {scenario_data['skill']}\n"

    if skill_group:
        text += f"📂 <b>Группа:</b> {skill_group}\n"

    text += f"📋 <b>Сценарий:</b> {scenario_name}\n"
    text += f"<i>{scenario_data['context']}</i>\n\n"
    text += f"🎯 <b>Цель:</b> {goal_name}\n"
    text += f"<i>{goal_data['description']}</i>\n\n"

    if not is_prem and first_game:
        text += (
            "🎁 <b>Бонус для первой игры:</b> расширенный анализ диалога.\n"
            "В следующих играх анализ будет сокращённым.\n"
            "💎 С премиум-подпиской — полный анализ всегда.\n\n"
        )
    elif not is_prem:
        games = get_games_this_month(user_id)
        remaining = MAX_FREE_GAMES - games
        text += f"🆓 Осталось бесплатных игр в этом месяце: {remaining}\n\n"
    else:
        text += "💎 Премиум-подписка: полный анализ диалога.\n\n"

    text += "Сейчас оппонент начнёт диалог. Поехали! 🚀\n\n"
    text += "Для завершения нажми кнопку ниже или отправь /stop"

    return text


# === Веб-сервер-заглушка ===

async def health_check(request):
    return web.Response(text="OK")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/yookassa-webhook", yookassa_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    logger.info("Health check server started on port 10000")

async def yookassa_webhook(request):
    """Webhook для ЮKassa."""
    import json
    from payment import process_payment_callback

    try:
        data = await request.json()
        result = await process_payment_callback(data)

        if result.get("success"):
            # Уведомляем пользователя в Telegram
            user_id = result.get("user_id")
            if user_id:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            "✅ <b>Оплата получена!</b>\n\n"
                            "Премиум-подписка активирована на 30 дней. 💎"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(text="Error", status=500)


# === Вспомогательные функции ===

async def delete_selection_messages(message: types.Message, user_id: int):
    """Удаляет все сообщения с выбором вариантов для пользователя."""
    if user_id in user_selection_messages:
        for msg_id in user_selection_messages[user_id]:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception:
                pass
        user_selection_messages[user_id] = []


def add_selection_message(user_id: int, message_id: int):
    """Добавляет ID сообщения в список для последующего удаления."""
    if user_id not in user_selection_messages:
        user_selection_messages[user_id] = []
    user_selection_messages[user_id].append(message_id)


# === Обработчики ===

@dp.message(lambda msg: msg.text == "🚀 Старт")
async def start_button(message: types.Message, state: FSMContext):
    await start_game(message, state)


@dp.message(lambda msg: msg.text == "🎲 Случайная игра")
async def random_game_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if not is_premium(user_id):
        await message.answer(
            "💎 Случайная игра доступна только с премиум-подпиской.\n"
            "Нажми «💎 Подписка», чтобы оформить.",
            reply_markup=get_start_keyboard(user_id),
        )
        return

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]
    if user_id in user_reply_count:
        del user_reply_count[user_id]

    await delete_selection_messages(message, user_id)

    role_name = random.choice(list(ROLES.keys()))
    scenario_name = random.choice(list(SCENARIOS.keys()))
    goal_name = random.choice(list(GOALS.keys()))

    logger.info(
        f"Пользователь {user_id} запустил случайную игру: "
        f"роль={role_name}, сценарий={scenario_name}, цель={goal_name}"
    )

    if not can_play(user_id, MAX_FREE_GAMES):
        games = get_games_this_month(user_id)
        await message.answer(
            f"⚠️ <b>Лимит игр исчерпан!</b>\n\n"
            f"Вы использовали {games}/{MAX_FREE_GAMES} бесплатных диалогов.\n"
            "💎 Оформите премиум-подписку.",
            parse_mode="HTML",
            reply_markup=get_start_keyboard(user_id),
        )
        return

    config_text = get_final_config_text(role_name, scenario_name, goal_name, user_id)
    config_msg = await message.answer(config_text, parse_mode="HTML")
    add_selection_message(user_id, config_msg.message_id)

    await launch_game(message, role_name, scenario_name, goal_name, state)


@dp.message(lambda msg: msg.text == "💎 Подписка")
async def subscription_button(message: types.Message):
    """Обработка кнопки Подписка."""
    user_id = message.from_user.id

    if is_premium(user_id):
        # Уже есть подписка — показываем статус
        if is_test_user(user_id):
            expires = "вечная (тестовый доступ)"
        else:
            row = fetch_one(
                "SELECT subscription_expires FROM users WHERE user_id = ?",
                (user_id,)
            )
            expires = row["subscription_expires"] if row and row["subscription_expires"] else "неизвестно"

        await message.answer(
            f"💎 <b>У вас активна премиум-подписка!</b>\n\n"
            f"📅 Действует до: {expires}\n\n"
            "Вам доступны все роли (6), сценарии (20) и цели (6), "
            "а также случайная игра и безлимитные диалоги.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user_id),
        )
    else:
        # Нет подписки — показываем варианты
        from payment import is_yookassa_configured, get_subscription_price

        if is_yookassa_configured():
            # Показываем кнопку оплаты
            price = get_subscription_price()
            pay_keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(
                        text=f"💳 Оплатить {price:.0f} ₽",
                        callback_data="pay_subscription"
                    )],
                ]
            )
            await message.answer(
                "💎 <b>Премиум-подписка</b>\n\n"
                "С подпиской вы получаете:\n"
                "• 6 ролей оппонента (вместо 3)\n"
                "• 20 сценариев (вместо 10)\n"
                "• 6 целей (вместо 3)\n"
                "• Случайную игру\n"
                "• Безлимитные диалоги\n"
                "• Расширенный анализ каждого диалога\n\n"
                f"💰 Стоимость: <b>{price:.0f} ₽ / 30 дней</b>\n\n"
                "Нажмите кнопку ниже, чтобы оплатить:",
                parse_mode="HTML",
                reply_markup=pay_keyboard,
            )
        else:
            # ЮKassa не настроена — показываем заглушку
            games = get_games_this_month(user_id)
            remaining = MAX_FREE_GAMES - games
            await message.answer(
                "💎 <b>Премиум-подписка</b>\n\n"
                "С подпиской вы получаете:\n"
                "• 6 ролей оппонента (вместо 3)\n"
                "• 20 сценариев (вместо 10)\n"
                "• 6 целей (вместо 3)\n"
                "• Случайную игру\n"
                "• Безлимитные диалоги\n\n"
                f"🆓 У вас осталось {remaining}/{MAX_FREE_GAMES} бесплатных игр.\n\n"
                "💰 Стоимость: 299 ₽ / месяц\n\n"
                "Оплата скоро будет доступна.",
                parse_mode="HTML",
                reply_markup=get_start_keyboard(user_id),
            )

@dp.callback_query(lambda c: c.data == "pay_subscription")
async def process_pay_subscription(callback: types.CallbackQuery):
    """Обработка нажатия на кнопку оплаты."""
    user_id = callback.from_user.id

    await callback.answer()

    from payment import create_subscription_payment, is_yookassa_configured

    if not is_yookassa_configured():
        await callback.message.answer(
            "⚠️ Оплата временно недоступна. Попробуйте позже.",
            parse_mode="HTML",
        )
        return

    result = await create_subscription_payment(user_id)

    if result["success"]:
        # Показываем ссылку на оплату
        pay_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(
                    text="💳 Перейти к оплате",
                    url=result["confirmation_url"]
                )],
                [types.InlineKeyboardButton(
                    text="✅ Я оплатил",
                    callback_data=f"check_payment:{result['payment_id']}"
                )],
            ]
        )

        await callback.message.answer(
            f"💳 <b>Счёт создан!</b>\n\n"
            f"Сумма: {result['amount']:.2f} ₽\n"
            f"Нажмите кнопку ниже, чтобы оплатить.\n"
            f"После оплаты нажмите «✅ Я оплатил».",
            parse_mode="HTML",
            reply_markup=pay_keyboard,
        )
    else:
        await callback.message.answer(
            f"❌ Ошибка при создании платежа: {result.get('error', 'неизвестная ошибка')}",
            parse_mode="HTML",
        )


@dp.callback_query(lambda c: c.data.startswith("check_payment:"))
async def process_check_payment(callback: types.CallbackQuery):
    """Проверка статуса платежа после нажатия «Я оплатил»."""
    user_id = callback.from_user.id
    payment_id = callback.data.split(":")[1]

    await callback.answer("Проверяю...")

    from payment import check_payment_status

    status = await check_payment_status(payment_id)

    if status == "succeeded":
        # Активируем подписку СРАЗУ
        from subscription import add_subscription
        add_subscription(user_id, days=30)

        # Обновляем статус платежа в БД
        from database import execute_sql
        execute_sql(
            "UPDATE payments SET status = 'paid' WHERE payment_id = ?",
            (payment_id,)
        )

        await callback.message.answer(
            "✅ <b>Оплата прошла успешно!</b>\n\n"
            "Премиум-подписка активирована на 30 дней.\n"
            "Наслаждайтесь всеми возможностями! 💎",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user_id),
        )
    elif status == "pending":
        await callback.message.answer(
            "⏳ <b>Платёж ещё в обработке.</b>\n\n"
            "Обычно это занимает 1-2 минуты.\n"
            "Нажмите «✅ Я оплатил» ещё раз через минуту.",
            parse_mode="HTML",
        )
    else:
        await callback.message.answer(
            f"⚠️ Статус платежа: {status}\n"
            "Если вы оплатили, но подписка не активировалась, "
            "напишите администратору.",
            parse_mode="HTML",
        )

@dp.message(lambda msg: msg.text == "🛑 Завершить диалог")
async def stop_button(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
    else:
        await cancel_setup(message, state)


@dp.message(lambda msg: msg.text == "🛑 Прервать")
async def cancel_button(message: types.Message, state: FSMContext):
    await cancel_setup(message, state)


@dp.message(lambda msg: msg.text == "↩️ Назад")
async def back_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()

    add_selection_message(user_id, message.message_id)

    if current_state == GameSetup.waiting_for_scenario.state:
        await state.set_state(GameSetup.waiting_for_role)
        msg = await message.answer(
            build_role_text(user_id),
            parse_mode="HTML",
            reply_markup=build_role_keyboard(user_id),
        )
        add_selection_message(user_id, msg.message_id)

    elif current_state == GameSetup.waiting_for_goal.state:
        user_data = await state.get_data()
        role_name = user_data.get("role_name", "...")
        await state.set_state(GameSetup.waiting_for_scenario)
        msg = await message.answer(
            build_scenario_text(role_name, user_id),
            parse_mode="HTML",
            reply_markup=build_scenario_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)


@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    await start_game(message, state)


async def start_game(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

        # Проверяем незавершённые платежи (если ЮKassa настроена)
    from payment import is_yookassa_configured
    if is_yookassa_configured():
        from database import fetch_all
        pending_payments = fetch_all(
            "SELECT payment_id FROM payments WHERE user_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if pending_payments:
            payment_id = pending_payments[0]["payment_id"]
            from payment import check_payment_status
            status = await check_payment_status(payment_id)
            if status == "succeeded":
                from subscription import add_subscription
                add_subscription(user_id, days=30)
                execute_sql(
                    "UPDATE payments SET status = 'paid' WHERE payment_id = ?",
                    (payment_id,)
                )
                logger.info(f"Подписка пользователя {user_id} активирована автоматически после проверки")

    await delete_selection_messages(message, user_id)

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]
    if user_id in user_reply_count:
        del user_reply_count[user_id]

    first_time = ensure_user_exists(user_id, message.from_user.username)
    update_activity(user_id)

    welcome_text = get_welcome_text(user_id, first_time)
    msg = await message.answer(welcome_text, parse_mode="HTML", reply_markup=build_role_keyboard(user_id))
    add_selection_message(user_id, msg.message_id)

    await state.set_state(GameSetup.waiting_for_role)


async def launch_game(message: types.Message, role_name: str, scenario_name: str, goal_name: str, state: FSMContext):
    user_id = message.from_user.id

    # Проверяем лимит игр
    if not can_play(user_id, MAX_FREE_GAMES):
        games = get_games_this_month(user_id)
        await message.answer(
            f"⚠️ <b>Лимит игр исчерпан!</b>\n\n"
            f"Вы использовали {games}/{MAX_FREE_GAMES} бесплатных диалогов в этом месяце.\n"
            "💎 Оформите премиум-подписку для безлимитного доступа.\n"
            "Нажмите «💎 Подписка» в главном меню.",
            parse_mode="HTML",
            reply_markup=get_start_keyboard(user_id),
        )
        return

    role_prompt = ROLES[role_name]["prompt"]
    scenario_context = SCENARIOS[scenario_name]["context"]
    goal_criteria = GOALS[goal_name]["criteria"]

    # Определяем тип анализа
    user_is_premium = is_premium(user_id)
    first_game = is_first_game(user_id)
    full_analysis = user_is_premium or first_game

    system_prompt = build_system_prompt(
        role_prompt=role_prompt,
        scenario_context=scenario_context,
        goal_criteria=goal_criteria,
        is_premium=full_analysis,
    )

    user_messages[user_id] = [{"role": "system", "content": system_prompt}]
    user_reply_count[user_id] = 0
    user_game_data[user_id] = {
        "role_name": role_name,
        "scenario_name": scenario_name,
        "goal_name": goal_name,
    }

    # Обновляем БД
    execute_sql("UPDATE users SET has_played = 1 WHERE user_id = ?", (user_id,))

    # Увеличиваем счётчик игр
    increment_games_this_month(user_id)

        # Удаляем предыдущие сообщения с выбором
    await delete_selection_messages(message, user_id)

    # Формируем текст конфигурации
    config_text = get_final_config_text(role_name, scenario_name, goal_name, user_id)

    # Запрашиваем ПРЕДЫСТОРИЮ
    user_messages[user_id].append({
        "role": "user",
        "content": (
            "Выдай предысторию согласно инструкции (ЭТАП 1 — ПРЕДЫСТОРИЯ). "
            "Опиши 3-5 предложениями, что произошло за последние 24-48 часов "
            "и почему этот разговор происходит сейчас. "
            "Не начинай диалог — жди сигнала «Готов»."
        )
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    intro_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": intro_response})

    # Объединяем конфигурацию и предысторию в ОДНО сообщение
    combined_text = config_text + "\n" + "—" * 30 + "\n\n" + intro_response

    ready_keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="✅ Готов")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми Готов, чтобы начать диалог",
    )

    # Отправляем объединённое сообщение и НЕ добавляем его в список удаляемых
    await message.answer(combined_text, parse_mode="HTML", reply_markup=ready_keyboard)

    await state.set_state(GameSetup.waiting_for_ready)

@dp.message(StateFilter(GameSetup.waiting_for_ready))
async def process_ready(message: types.Message, state: FSMContext):
    """Обработка нажатия кнопки Готов — запуск диалога."""
    user_id = message.from_user.id

    if message.text != "✅ Готов":
        await message.answer(
            "👆 Нажми кнопку <b>«✅ Готов»</b>, чтобы начать диалог.",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="✅ Готов")]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return

    # Убираем клавиатуру и начинаем диалог
    await message.answer(
        "🚀 <b>Диалог начинается!</b>",
        parse_mode="HTML",
        reply_markup=get_stop_keyboard(),
    )

    # Запрашиваем первую реплику оппонента
    user_messages[user_id].append({
        "role": "user",
        "content": "Готов. Начинаем диалог. Твоя первая реплика."
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ai_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": ai_response})

    await message.answer(ai_response)
    await state.set_state(GameSetup.in_dialog)

@dp.message(StateFilter(GameSetup.waiting_for_role))
async def process_role(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    button_text = message.text

    add_selection_message(user_id, message.message_id)

    if button_text not in ROLE_BUTTONS:
        msg = await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать роль.",
            reply_markup=build_role_keyboard(user_id),
        )
        add_selection_message(user_id, msg.message_id)
        return

    role_name = ROLE_BUTTONS[button_text]

    available_roles = get_available_roles(user_id, ROLES)
    if role_name not in available_roles:
        msg = await message.answer(
            "🔒 Эта роль доступна только с премиум-подпиской.\n"
            "Нажми «💎 Подписка» в главном меню, чтобы оформить.",
            reply_markup=build_role_keyboard(user_id),
        )
        add_selection_message(user_id, msg.message_id)
        return

    await state.update_data(role_name=role_name)
    logger.info(f"Пользователь {user_id} выбрал роль: {role_name}")

    sent_msg = await message.answer(
        build_scenario_text(role_name, user_id),
        parse_mode="HTML",
        reply_markup=build_scenario_keyboard(),
    )
    add_selection_message(user_id, sent_msg.message_id)
    await state.set_state(GameSetup.waiting_for_scenario)
        # После выбора роли — если premium, показываем группы
    if is_premium(user_id):
        from scenarios import SKILL_GROUPS
        groups_text = "🧠 <b>Шаг 2/4: Какой навык ты хочешь прокачать?</b>\n\n"
        groups_text += "Выбери группу — каждая группа содержит сценарии, "
        groups_text += "направленные на развитие конкретного навыка.\n\n"
        for key, data in SKILL_GROUPS.items():
            groups_text += f"<b>{key}: {data['name']}</b>\n{data['description']}\n\n"

        sent_msg = await message.answer(
            groups_text,
            parse_mode="HTML",
            reply_markup=build_group_keyboard(user_id),
        )
        add_selection_message(user_id, sent_msg.message_id)
        await state.set_state(GameSetup.waiting_for_skill_group)
    else:
        # Для бесплатных — сразу показываем их 5 сценариев
        from scenarios import get_free_scenarios
        free_scenarios = get_free_scenarios()
        scenarios_text = "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
        scenarios_text += "👇 <b>Нажми на одну из кнопок ниже:</b>\n\n"
        for i, (name, data) in enumerate(free_scenarios.items(), start=1):
            context = data["context"].replace("Контекст: ", "")
            scenarios_text += f"<b>Вариант {i}: {name}</b> ({data['skill']})\n{context}\n\n"

        # Создаём клавиатуру с бесплатными сценариями
        global SCENARIO_BUTTONS
        SCENARIO_BUTTONS = {}
        buttons = []
        for i, scenario_name in enumerate(free_scenarios.keys(), start=1):
            button_text = f"Вариант {i}"
            SCENARIO_BUTTONS[button_text] = scenario_name
            buttons.append([types.KeyboardButton(text=button_text)])

        buttons.append([types.KeyboardButton(text="↩️ Назад")])
        buttons.append([types.KeyboardButton(text="🛑 Прервать")])

        scenario_keyboard = types.ReplyKeyboardMarkup(
            keyboard=buttons,
            resize_keyboard=True,
            one_time_keyboard=True,
            input_field_placeholder="Нажми на кнопку ниже",
        )

        sent_msg = await message.answer(
            scenarios_text,
            parse_mode="HTML",
            reply_markup=scenario_keyboard,
        )
        add_selection_message(user_id, sent_msg.message_id)
        await state.set_state(GameSetup.waiting_for_scenario)


def build_group_keyboard(user_id: int):
    """Клавиатура выбора группы навыков."""
    global GROUP_BUTTONS
    GROUP_BUTTONS = {}

    from scenarios import SKILL_GROUPS

    buttons = []
    for group_key, group_data in SKILL_GROUPS.items():
        button_text = f"{group_key}: {group_data['name']}"
        GROUP_BUTTONS[button_text] = group_key
        buttons.append([types.KeyboardButton(text=button_text)])

    buttons.append([types.KeyboardButton(text="↩️ Назад")])
    buttons.append([types.KeyboardButton(text="🛑 Прервать")])

    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери навык",
    )

@dp.message(StateFilter(GameSetup.waiting_for_skill_group))
async def process_skill_group(message: types.Message, state: FSMContext):
    """Обработка выбора группы навыков."""
    user_id = message.from_user.id
    button_text = message.text

    add_selection_message(user_id, message.message_id)

    from scenarios import SKILL_GROUPS, get_scenarios_by_group

    if button_text not in GROUP_BUTTONS:
        msg = await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать навык.",
            reply_markup=build_group_keyboard(user_id),
        )
        add_selection_message(user_id, msg.message_id)
        return

    group_key = GROUP_BUTTONS[button_text]
    group_data = SKILL_GROUPS[group_key]
    group_scenarios = get_scenarios_by_group(group_key)

    await state.update_data(skill_group=group_data["name"])

    # Формируем текст со сценариями группы
    scenarios_text = f"🧠 <b>Навык:</b> {group_data['name']}\n"
    scenarios_text += f"<i>{group_data['description']}</i>\n\n"
    scenarios_text += "📋 <b>Шаг 3/4: Выбери сценарий</b>\n\n"
    scenarios_text += "👇 <b>Нажми на одну из кнопок ниже:</b>\n\n"

    # Создаём клавиатуру
    global SCENARIO_BUTTONS
    SCENARIO_BUTTONS = {}
    buttons = []
    for i, scenario_name in enumerate(group_scenarios.keys(), start=1):
        button_text = f"Вариант {i}"
        SCENARIO_BUTTONS[button_text] = scenario_name
        buttons.append([types.KeyboardButton(text=button_text)])

    buttons.append([types.KeyboardButton(text="↩️ Назад")])
    buttons.append([types.KeyboardButton(text="🛑 Прервать")])

    scenario_keyboard = types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )

    for i, (name, data) in enumerate(group_scenarios.items(), start=1):
        context = data["context"].replace("Контекст: ", "")
        scenarios_text += f"<b>Вариант {i}: {name}</b>\n{context}\n\n"

    sent_msg = await message.answer(
        scenarios_text,
        parse_mode="HTML",
        reply_markup=scenario_keyboard,
    )
    add_selection_message(user_id, sent_msg.message_id)
    await state.set_state(GameSetup.waiting_for_scenario)

@dp.message(StateFilter(GameSetup.waiting_for_scenario))
async def process_scenario(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    button_text = message.text

    add_selection_message(user_id, message.message_id)

    if button_text not in SCENARIO_BUTTONS:
        msg = await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать сценарий.",
            reply_markup=build_scenario_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    scenario_name = SCENARIO_BUTTONS[button_text]

    available_scenarios = get_available_scenarios(user_id, SCENARIOS)
    if scenario_name not in available_scenarios:
        msg = await message.answer(
            "🔒 Этот сценарий доступен только с премиум-подпиской.\n"
            "Нажми «💎 Подписка» в главном меню, чтобы оформить.",
            reply_markup=build_scenario_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    await state.update_data(scenario_name=scenario_name)
    logger.info(f"Пользователь {user_id} выбрал сценарий: {scenario_name}")

    sent_msg = await message.answer(
        build_goal_text(scenario_name, user_id),
        parse_mode="HTML",
        reply_markup=build_goal_keyboard(),
    )
    add_selection_message(user_id, sent_msg.message_id)
    await state.set_state(GameSetup.waiting_for_goal)


@dp.message(StateFilter(GameSetup.waiting_for_goal))
async def process_goal(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    button_text = message.text

    add_selection_message(user_id, message.message_id)

    if button_text not in GOAL_BUTTONS:
        msg = await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать цель.",
            reply_markup=build_goal_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    goal_name = GOAL_BUTTONS[button_text]

    available_goals = get_available_goals(user_id, GOALS)
    if goal_name not in available_goals:
        msg = await message.answer(
            "🔒 Эта цель доступна только с премиум-подпиской.\n"
            "Нажми «💎 Подписка» в главном меню, чтобы оформить.",
            reply_markup=build_goal_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    user_data = await state.get_data()
    role_name = user_data.get("role_name")
    scenario_name = user_data.get("scenario_name")

    if not role_name or not scenario_name:
        await message.answer(
            "⚠️ Что-то пошло не так. Нажми Старт, чтобы начать заново.",
            reply_markup=get_start_keyboard(user_id),
        )
        await state.clear()
        return

    await launch_game(message, role_name, scenario_name, goal_name, state)


@dp.message(StateFilter(GameSetup.in_dialog))
async def handle_dialog_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if message.text == "🛑 Завершить диалог":
        await finish_game(message, state)
        return
    if message.text == "/stop":
        await finish_game(message, state)
        return
    if user_id not in user_messages:
        await message.answer(
            "⚠️ Диалог не найден. Нажми Старт, чтобы начать заново.",
            reply_markup=get_start_keyboard(user_id),
        )
        await state.clear()
        return

    user_reply_count[user_id] = user_reply_count.get(user_id, 0) + 1
    user_messages[user_id].append({"role": "user", "content": message.text})

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ai_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": ai_response})

    await message.answer(ai_response)


async def finish_game(message: types.Message, state: FSMContext):
    """Завершение игры и запрос анализа."""
    user_id = message.from_user.id
    reply_count = user_reply_count.get(user_id, 0)

    if user_id not in user_messages or reply_count == 0:
        await cancel_setup(message, state)
        return

    await message.answer("🛑 Диалог завершён. Запрашиваю анализ игры...")

    user_messages[user_id].append({
        "role": "user",
        "content": "/stop. Выдай полный анализ игры согласно инструкции."
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    analysis = await get_ai_response(user_messages[user_id])

    await message.answer(analysis)

    # === НОВОЕ: Отправляем предложение оставить обратную связь ===
    if config.FEEDBACK_FORM_URL:
        feedback_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(
                    text="📝 Оставить отзыв",
                    url=config.FEEDBACK_FORM_URL
                )],
            ]
        )
        await message.answer(
            "📝 <b>Помогите сделать тренажёр лучше!</b>\n\n"
            "Пройдите короткий опрос — это займёт 2-3 минуты.\n"
            "Ваше мнение поможет улучшить сценарии, роли и анализ.\n\n"
            "👇 Нажмите кнопку ниже, чтобы открыть форму:",
            parse_mode="HTML",
            reply_markup=feedback_keyboard,
        )

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]
    if user_id in user_reply_count:
        del user_reply_count[user_id]

    await message.answer(
        "🎉 Игра окончена! Хочешь попробовать снова?",
        reply_markup=get_main_menu_keyboard(user_id),
    )
    logger.info(f"Пользователь {user_id} завершил игру с анализом")


async def cancel_setup(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]
    if user_id in user_reply_count:
        del user_reply_count[user_id]
    await delete_selection_messages(message, user_id)

    await message.answer(
        "🔄 Диалог прерван. Для начала диалога нажмите кнопку ниже.",
        reply_markup=get_start_keyboard(user_id),
    )
    logger.info(f"Пользователь {user_id} прервал игру без анализа")


@dp.message(Command("help"))
async def help_command(message: types.Message):
    user_id = message.from_user.id
    help_text = (
        "🎯 <b>Business Negotiation Trainer</b>\n\n"
        "<b>Как играть:</b>\n"
        "1. Нажми <b>Старт</b> для начала\n"
        "2. Нажимай на кнопки, чтобы выбрать роль, сценарий и цель\n"
        "3. На шагах 2 и 3 можно нажать <b>↩️ Назад</b> чтобы вернуться\n"
        "4. Веди диалог с ИИ-оппонентом\n"
        "5. Нажми <b>Завершить диалог</b> для остановки и получения анализа\n\n"
    )
    if is_premium(user_id):
        help_text += (
            "💎 <b>Премиум-функции:</b>\n"
            "• 6 ролей, 20 сценариев, 6 целей\n"
            "• Случайная игра\n"
            "• Безлимитные диалоги\n"
            "• Расширенный анализ\n\n"
        )
    else:
        games = get_games_this_month(user_id)
        remaining = MAX_FREE_GAMES - games
        help_text += (
            f"🆓 <b>Бесплатный доступ:</b>\n"
            f"• 3 роли, 10 сценариев, 3 цели\n"
            f"• {remaining}/{MAX_FREE_GAMES} диалогов в этом месяце\n"
            f"• Первый диалог — расширенный анализ\n\n"
            "💎 <b>Хочешь больше?</b> Нажми «Подписка».\n\n"
        )
    help_text += "Удачи в переговорах! 💼"

    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("stop"))
async def stop_anywhere(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
    elif current_state == GameSetup.waiting_for_ready.state:
        # Если остановка на этапе вводной — просто сбрасываем
        await cancel_setup(message, state)
    else:
        await cancel_setup(message, state)


# === Админ-панель ===

@dp.message(Command("admin"))
async def admin_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return

    await message.answer(
        "🔧 <b>Админ-панель</b>\n\n"
        "<b>Команды:</b>\n"
        "/add_test &lt;user_id&gt; — добавить тестового пользователя\n"
        "/remove_test &lt;user_id&gt; — убрать тестового\n"
        "/add_sub &lt;user_id&gt; &lt;дней&gt; — добавить подписку\n"
        "/remove_sub &lt;user_id&gt; — отозвать подписку\n"
        "/check_user &lt;user_id&gt; — проверить статус\n"
        "/stats — статистика\n"
        "/toggle_subscription — вкл/выкл режим подписки\n"
        "/export_db — скачать бекап базы данных",
        parse_mode="HTML",
    )


@dp.message(Command("add_test"))
async def add_test_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        user_id = int(message.text.split()[1])
        add_test_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} добавлен как тестовый (вечная премиум-подписка).")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /add_test <user_id>")


@dp.message(Command("remove_test"))
async def remove_test_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        user_id = int(message.text.split()[1])
        execute_sql("UPDATE users SET is_test_user = 0 WHERE user_id = ?", (user_id,))
        await message.answer(f"✅ Тестовый статус пользователя {user_id} убран.")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /remove_test <user_id>")


@dp.message(Command("add_sub"))
async def add_sub_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        from subscription import add_subscription
        parts = message.text.split()
        user_id = int(parts[1])
        days = int(parts[2])
        add_subscription(user_id, days)
        await message.answer(f"✅ Подписка пользователя {user_id} продлена на {days} дней.")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /add_sub <user_id> <дней>")


@dp.message(Command("remove_sub"))
async def remove_sub_command(message: types.Message):
    """Снимает подписку с пользователя: /remove_sub 123456789"""
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        user_id = int(message.text.split()[1])
        execute_sql(
            "UPDATE users SET subscription_expires = NULL WHERE user_id = ?",
            (user_id,)
        )
        await message.answer(f"✅ Подписка пользователя {user_id} отозвана.")
        logger.info(f"Admin {message.from_user.id} removed subscription for user {user_id}")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /remove_sub <user_id>")
        

@dp.message(Command("check_user"))
async def check_user_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        user_id = int(message.text.split()[1])
        premium = is_premium(user_id)
        test = is_test_user(user_id)

        status = "💎 Премиум" if premium else "🆓 Бесплатный"
        if test:
            status += " (тестовый — вечная подписка)"

        row = fetch_one(
            "SELECT subscription_expires, created_at, last_activity, has_played, games_this_month "
            "FROM users WHERE user_id = ?",
            (user_id,)
        )

        expires = row["subscription_expires"] if row and row["subscription_expires"] else "Нет"
        created = row["created_at"] if row else "Нет данных"
        last_act = row["last_activity"] if row and row["last_activity"] else "Нет данных"
        has_played = "Да" if row and row["has_played"] else "Нет"
        games = row["games_this_month"] if row else 0

        await message.answer(
            f"👤 <b>Пользователь:</b> {user_id}\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"📅 <b>Подписка до:</b> {expires}\n"
            f"🎮 <b>Играл ранее:</b> {has_played}\n"
            f"🔢 <b>Игр в этом месяце:</b> {games}\n"
            f"🕐 <b>Создан:</b> {created}\n"
            f"🕑 <b>Активность:</b> {last_act}",
            parse_mode="HTML",
        )
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /check_user <user_id>")


@dp.message(Command("stats"))
async def stats_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return

    from database import fetch_all

    total = fetch_all("SELECT COUNT(*) as c FROM users")[0]["c"]

    premium_row = fetch_all(
        "SELECT COUNT(*) as c FROM users "
        "WHERE subscription_expires > datetime('now') OR is_test_user = 1"
    )
    premium = premium_row[0]["c"] if premium_row else 0

    test_row = fetch_all("SELECT COUNT(*) as c FROM users WHERE is_test_user = 1")
    test = test_row[0]["c"] if test_row else 0

    played_row = fetch_all("SELECT COUNT(*) as c FROM users WHERE has_played = 1")
    played = played_row[0]["c"] if played_row else 0

    games_row = fetch_all("SELECT SUM(games_this_month) as c FROM users")
    total_games = games_row[0]["c"] if games_row and games_row[0]["c"] else 0

    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"💎 Премиум: <b>{premium}</b>\n"
        f"🧪 Тестовых: <b>{test}</b>\n"
        f"🎮 Играли хотя бы раз: <b>{played}</b>\n"
        f"🔢 Всего игр в этом месяце: <b>{total_games}</b>\n"
        f"🆓 Бесплатных: <b>{total - premium}</b>",
        parse_mode="HTML",
    )


@dp.message(Command("toggle_subscription"))
async def toggle_subscription_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return

    row = fetch_one("SELECT value FROM settings WHERE key = 'subscription_enabled'")
    current = row["value"] if row else "1"
    new_value = "0" if current == "1" else "1"

    execute_sql(
        "INSERT INTO settings (key, value) VALUES ('subscription_enabled', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (new_value, new_value)
    )

    status = "✅ ВКЛЮЧЕН" if new_value == "1" else "⛔ ВЫКЛЮЧЕН"
    await message.answer(f"Режим подписки: {status}")


@dp.message(Command("export_db"))
async def export_db_command(message: types.Message):
    """Экспортирует базу данных в SQL-формате."""
    if message.from_user.id not in ADMIN_USER_IDS:
        return

    from subscription import export_users_to_sql
    sql_data = export_users_to_sql()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
        f.write(sql_data)
        temp_path = f.name

    await message.answer_document(
        types.FSInputFile(temp_path, filename="backup.sql"),
        caption="📦 Бекап базы данных (SQL формат)"
    )

    os.unlink(temp_path)


@dp.message()
async def unexpected_message(message: types.Message):
    user_id = message.from_user.id
    await message.answer(
        "👋 Добро пожаловать! Нажмите кнопку ниже, чтобы начать тренировку.",
        reply_markup=get_start_keyboard(user_id),
    )


async def main():
    init_db()
    logger.info("Бот запускается...")
    await asyncio.gather(
        run_web_server(),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
