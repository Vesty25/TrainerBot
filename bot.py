import asyncio
import logging
import random

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
from database import init_db
from subscription import (
    is_premium, is_test_user, ensure_user_exists,
    update_activity, get_available_roles,
    get_available_scenarios, get_available_goals,
    add_test_user, has_played_before,
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


def build_role_keyboard():
    """Клавиатура выбора роли."""
    global ROLE_BUTTONS
    ROLE_BUTTONS = {}
    buttons = []
    for i, role_name in enumerate(ROLES.keys(), start=1):
        button_text = f"Вариант {i}"
        ROLE_BUTTONS[button_text] = role_name
        buttons.append([types.KeyboardButton(text=button_text)])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def build_scenario_keyboard():
    """Клавиатура выбора сценария."""
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

    return types.ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def build_goal_keyboard():
    """Клавиатура выбора цели."""
    global GOAL_BUTTONS
    GOAL_BUTTONS = {}
    buttons = []
    for i, goal_name in enumerate(GOALS.keys(), start=1):
        button_text = f"Вариант {i}"
        GOAL_BUTTONS[button_text] = goal_name
        buttons.append([types.KeyboardButton(text=button_text)])
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

    if first_time:
        greeting = "👋 <b>Привет!</b> Я — тренажёр деловых переговоров.\n\n"
    else:
        greeting = "👋 <b>С возвращением!</b> Давай сыграем ещё раз.\n\n"

    greeting += "Сейчас мы настроим игру. На каждом шаге просто нажимай на кнопку.\n\n"

    if not is_prem:
        greeting += (
            "💎 <b>Хочешь больше?</b>\n"
            "С премиум-подпиской доступны:\n"
            "• 6 ролей оппонента (сейчас 3)\n"
            "• 20 сценариев (сейчас 10)\n"
            "• 6 целей (сейчас 3)\n"
            "• Случайная игра\n\n"
            "Нажми <b>«💎 Подписка»</b> в главном меню.\n\n"
        )

    greeting += build_role_text(user_id)
    return greeting


def get_final_config_text(role_name: str, scenario_name: str, goal_name: str):
    """Финальное сообщение с выбранными параметрами."""
    role_data = ROLES[role_name]
    scenario_data = SCENARIOS[scenario_name]
    goal_data = GOALS[goal_name]

    text = "✅ <b>Игра настроена!</b>\n\n"
    text += f"🎭 <b>Роль оппонента:</b> {role_name}\n"
    text += f"<i>{role_data['description']}</i>\n\n"
    text += f"📋 <b>Сценарий:</b> {scenario_name}\n"
    text += f"<i>{scenario_data['context']}</i>\n\n"
    text += f"🎯 <b>Цель:</b> {goal_name}\n"
    text += f"<i>{goal_data['description']}</i>\n\n"
    text += "Сейчас оппонент начнёт диалог. Поехали! 🚀\n\n"
    text += "Для завершения нажми кнопку ниже или отправь /stop"

    return text


# === Веб-сервер-заглушка для Render ===

async def health_check(request):
    return web.Response(text="OK")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    logger.info("Health check server started on port 10000")


# === Вспомогательные функции ===

async def delete_selection_messages(message: types.Message, user_id: int):
    """Удаляет все сообщения с выбором вариантов для пользователя."""
    if user_id in user_selection_messages:
        for msg_id in user_selection_messages[user_id]:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception:
                pass  # сообщение могло быть уже удалено
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
    """Обработка кнопки Случайная игра (только premium)."""
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

    # Случайный выбор
    role_name = random.choice(list(ROLES.keys()))
    scenario_name = random.choice(list(SCENARIOS.keys()))
    goal_name = random.choice(list(GOALS.keys()))

    logger.info(
        f"Пользователь {user_id} запустил случайную игру: "
        f"роль={role_name}, сценарий={scenario_name}, цель={goal_name}"
    )

    # Показываем конфигурацию и запускаем
    config_text = get_final_config_text(role_name, scenario_name, goal_name)
    config_msg = await message.answer(config_text, parse_mode="HTML")
    add_selection_message(user_id, config_msg.message_id)

    await launch_game(message, role_name, scenario_name, goal_name, state)


@dp.message(lambda msg: msg.text == "💎 Подписка")
async def subscription_button(message: types.Message):
    """Обработка кнопки Подписка."""
    user_id = message.from_user.id

    if is_premium(user_id):
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT subscription_expires FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.close()

        expires = "вечная (тестовый доступ)" if is_test_user(user_id) else \
                  (row["subscription_expires"] if row and row["subscription_expires"] else "неизвестно")

        await message.answer(
            f"💎 <b>У вас активна премиум-подписка!</b>\n\n"
            f"📅 Действует до: {expires}\n\n"
            "Вам доступны все роли (6), сценарии (20) и цели (6), "
            "а также случайная игра.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(user_id),
        )
    else:
        await message.answer(
            "💎 <b>Премиум-подписка</b>\n\n"
            "С подпиской вы получаете:\n"
            "• 6 ролей оппонента (вместо 3)\n"
            "• 20 сценариев (вместо 10)\n"
            "• 6 целей (вместо 3)\n"
            "• Случайную игру\n\n"
            "💰 Стоимость: 299 ₽ / месяц\n\n"
            "Для оформления подписки напишите администратору: @your_username",
            parse_mode="HTML",
            reply_markup=get_start_keyboard(user_id),
        )


@dp.message(lambda msg: msg.text == "🛑 Завершить диалог")
async def stop_button(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
    else:
        await cancel_setup(message, state)


@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    await start_game(message, state)


async def start_game(message: types.Message, state: FSMContext):
    """Общая логика запуска игры."""
    user_id = message.from_user.id

    # Удаляем предыдущие сообщения с выбором
    await delete_selection_messages(message, user_id)

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]
    if user_id in user_reply_count:
        del user_reply_count[user_id]

    # Обновляем данные пользователя
    first_time = ensure_user_exists(user_id, message.from_user.username)
    update_activity(user_id)

    # Показываем приветствие
    welcome_text = get_welcome_text(user_id, first_time)
    msg = await message.answer(welcome_text, parse_mode="HTML", reply_markup=build_role_keyboard())
    add_selection_message(user_id, msg.message_id)

    await state.set_state(GameSetup.waiting_for_role)


async def launch_game(message: types.Message, role_name: str, scenario_name: str, goal_name: str, state: FSMContext):
    """Запускает игру с заданными параметрами."""
    user_id = message.from_user.id

    role_prompt = ROLES[role_name]["prompt"]
    scenario_context = SCENARIOS[scenario_name]["context"]
    goal_criteria = GOALS[goal_name]["criteria"]

    system_prompt = build_system_prompt(
        role_prompt=role_prompt,
        scenario_context=scenario_context,
        goal_criteria=goal_criteria,
    )

    user_messages[user_id] = [{"role": "system", "content": system_prompt}]
    user_reply_count[user_id] = 0
    user_game_data[user_id] = {
        "role_name": role_name,
        "scenario_name": scenario_name,
        "goal_name": goal_name,
    }

    # Отмечаем, что пользователь играл
    from database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET has_played = 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()

    # Удаляем сообщения с выбором
    await delete_selection_messages(message, user_id)

    # Отправляем финальную конфигурацию
    config_text = get_final_config_text(role_name, scenario_name, goal_name)
    config_msg = await message.answer(config_text, parse_mode="HTML", reply_markup=get_stop_keyboard())
    add_selection_message(user_id, config_msg.message_id)

    user_messages[user_id].append({
        "role": "user",
        "content": "Начни диалог своей первой репликой согласно сценарию и роли."
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

    # Сохраняем ID сообщения пользователя для удаления
    add_selection_message(user_id, message.message_id)

    if button_text not in ROLE_BUTTONS:
        msg = await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать роль.",
            reply_markup=build_role_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    role_name = ROLE_BUTTONS[button_text]

    # Проверяем доступность роли
    available_roles = get_available_roles(user_id, ROLES)
    if role_name not in available_roles:
        msg = await message.answer(
            "🔒 Эта роль доступна только с премиум-подпиской.\n"
            "Нажми «💎 Подписка» в главном меню, чтобы оформить.",
            reply_markup=build_role_keyboard(),
        )
        add_selection_message(user_id, msg.message_id)
        return

    await state.update_data(role_name=role_name)
    logger.info(f"Пользователь {user_id} выбрал роль: {role_name}")

    # Сохраняем ID сообщения бота
    sent_msg = await message.answer(
        build_scenario_text(role_name, user_id),
        parse_mode="HTML",
        reply_markup=build_scenario_keyboard(),
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
        "3. Веди диалог с ИИ-оппонентом\n"
        "4. Нажми <b>Завершить диалог</b> для остановки и получения анализа\n\n"
    )
    if is_premium(user_id):
        help_text += (
            "💎 <b>Премиум-функции:</b>\n"
            "• 6 ролей, 20 сценариев, 6 целей\n"
            "• Случайная игра\n\n"
        )
    else:
        help_text += (
            "💎 <b>Хочешь больше?</b> Нажми «Подписка» для доступа к 6 ролям, "
            "20 сценариям и случайной игре.\n\n"
        )
    help_text += "Удачи в переговорах! 💼"

    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("stop"))
async def stop_anywhere(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
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
        "/check_user &lt;user_id&gt; — проверить статус\n"
        "/stats — статистика\n"
        "/toggle_subscription — вкл/выкл режим подписки",
        parse_mode="HTML",
    )


@dp.message(Command("add_test"))
async def add_test_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        user_id = int(message.text.split()[1])
        add_test_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} добавлен как тестовый (вечная подписка).")
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /add_test <user_id>")


@dp.message(Command("remove_test"))
async def remove_test_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        from database import get_connection
        user_id = int(message.text.split()[1])
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_test_user = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
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


@dp.message(Command("check_user"))
async def check_user_command(message: types.Message):
    if message.from_user.id not in ADMIN_USER_IDS:
        return
    try:
        from database import get_connection
        user_id = int(message.text.split()[1])
        premium = is_premium(user_id)
        test = is_test_user(user_id)

        status = "💎 Премиум" if premium else "🆓 Бесплатный"
        if test:
            status += " (тестовый)"

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT subscription_expires, created_at, last_activity, has_played FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.close()

        expires = row["subscription_expires"] if row and row["subscription_expires"] else "Нет"
        created = row["created_at"] if row else "Нет данных"
        last_act = row["last_activity"] if row and row["last_activity"] else "Нет данных"
        has_played = "Да" if row and row["has_played"] else "Нет"

        await message.answer(
            f"👤 <b>Пользователь:</b> {user_id}\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"📅 <b>Подписка до:</b> {expires}\n"
            f"🎮 <b>Играл ранее:</b> {has_played}\n"
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
    from database import get_connection
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM users")
    total = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) as premium FROM users "
        "WHERE subscription_expires > datetime('now') OR is_test_user = 1"
    )
    premium = cursor.fetchone()["premium"]

    cursor.execute("SELECT COUNT(*) as test FROM users WHERE is_test_user = 1")
    test = cursor.fetchone()["test"]

    cursor.execute("SELECT COUNT(*) as played FROM users WHERE has_played = 1")
    played = cursor.fetchone()["played"]

    conn.close()

    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"💎 Премиум: <b>{premium}</b>\n"
        f"🧪 Тестовых: <b>{test}</b>\n"
        f"🎮 Играли хотя бы раз: <b>{played}</b>\n"
        f"🆓 Бесплатных: <b>{total - premium}</b>",
        parse_mode="HTML",
    )


@dp.message
