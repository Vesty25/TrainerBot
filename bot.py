import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, StateFilter
from aiogram.client.session.aiohttp import AiohttpSession

import config
from states import GameSetup
from roles import ROLES
from scenarios import SCENARIOS
from goals import GOALS
from prompts import build_system_prompt
from deepseek_client import get_ai_response


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

# Константные словари для кнопок (заполняются ОДИН раз)
ROLE_OPTIONS = {}      # "Вариант 1" -> "Требовательный Заказчик (Дмитрий)"
SCENARIO_OPTIONS = {}  # "Вариант 1" -> "Срыв сроков"
GOAL_OPTIONS = {}      # "Вариант 1" -> "Отстоять границы и сказать «нет»"


def init_options():
    """Заполняет словари кнопок (вызывается один раз при старте)."""
    global ROLE_OPTIONS, SCENARIO_OPTIONS, GOAL_OPTIONS

    ROLE_OPTIONS = {}
    for i, role_name in enumerate(ROLES.keys(), start=1):
        ROLE_OPTIONS[f"Вариант {i}"] = role_name

    SCENARIO_OPTIONS = {}
    for i, scenario_name in enumerate(SCENARIOS.keys(), start=1):
        SCENARIO_OPTIONS[f"Вариант {i}"] = scenario_name

    GOAL_OPTIONS = {}
    for i, goal_name in enumerate(GOALS.keys(), start=1):
        GOAL_OPTIONS[f"Вариант {i}"] = goal_name


def get_role_keyboard():
    """Клавиатура выбора роли."""
    buttons = [[types.KeyboardButton(text=key)] for key in ROLE_OPTIONS.keys()]
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def get_scenario_keyboard():
    """Клавиатура выбора сценария."""
    buttons = [[types.KeyboardButton(text=key)] for key in SCENARIO_OPTIONS.keys()]
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def get_goal_keyboard():
    """Клавиатура выбора цели."""
    buttons = [[types.KeyboardButton(text=key)] for key in GOAL_OPTIONS.keys()]
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажми на кнопку ниже",
    )


def build_role_text():
    """Текст с описанием ролей."""
    text = "🎭 <b>Шаг 1/3: Выбери роль оппонента</b>\n\n"
    for i, (role_name, role_data) in enumerate(ROLES.items(), start=1):
        text += f"<b>Вариант {i}: {role_name}</b>\n{role_data['description']}\n\n"
    return text


def build_scenario_text(selected_role: str):
    """Текст с описанием сценариев."""
    text = f"✅ <b>Роль оппонента:</b> {selected_role}\n\n"
    text += "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
    for i, (name, data) in enumerate(SCENARIOS.items(), start=1):
        context = data["context"].replace("Контекст: ", "")
        if len(context) > 150:
            context = context[:150].rsplit(".", 1)[0] + "."
        text += f"<b>Вариант {i}: {name}</b>\n{context}\n\n"
    return text


def build_goal_text(selected_scenario: str):
    """Текст с описанием целей."""
    text = f"✅ <b>Сценарий:</b> {selected_scenario}\n\n"
    text += "🎯 <b>Шаг 3/3: Выбери свою основную цель</b>\n\n"
    for i, (goal_name, goal_data) in enumerate(GOALS.items(), start=1):
        text += f"<b>Вариант {i}: {goal_name}</b>\n{goal_data['description']}\n\n"
    return text


# === Обработчики ===

@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Начало — Шаг 1: выбор роли оппонента."""
    user_id = message.from_user.id

    # Полный сброс всего
    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    await message.answer(
        "👋 <b>Привет!</b> Я — тренажёр деловых переговоров с игровой механикой скрытых целей.\n\n"
        "Сейчас мы настроим игру.\n\n"
        + build_role_text(),
        parse_mode="HTML",
        reply_markup=get_role_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_role)


@dp.message(StateFilter(GameSetup.waiting_for_role))
async def process_role(message: types.Message, state: FSMContext):
    """Обработка выбора роли."""
    button_text = message.text

    if button_text not in ROLE_OPTIONS:
        await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать роль.",
            reply_markup=get_role_keyboard(),
        )
        return

    role_name = ROLE_OPTIONS[button_text]
    await state.update_data(role_name=role_name)

    logger.info(f"Пользователь {message.from_user.id} выбрал роль: {role_name}")

    await message.answer(
        build_scenario_text(role_name),
        parse_mode="HTML",
        reply_markup=get_scenario_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_scenario)


@dp.message(StateFilter(GameSetup.waiting_for_scenario))
async def process_scenario(message: types.Message, state: FSMContext):
    """Обработка выбора сценария."""
    button_text = message.text

    if button_text not in SCENARIO_OPTIONS:
        await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать сценарий.",
            reply_markup=get_scenario_keyboard(),
        )
        return

    scenario_name = SCENARIO_OPTIONS[button_text]
    await state.update_data(scenario_name=scenario_name)

    logger.info(f"Пользователь {message.from_user.id} выбрал сценарий: {scenario_name}")

    await message.answer(
        build_goal_text(scenario_name),
        parse_mode="HTML",
        reply_markup=get_goal_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_goal)


@dp.message(StateFilter(GameSetup.waiting_for_goal))
async def process_goal(message: types.Message, state: FSMContext):
    """Обработка выбора цели и запуск игры."""
    button_text = message.text

    if button_text not in GOAL_OPTIONS:
        await message.answer(
            "❌ Пожалуйста, нажми на одну из кнопок ниже, чтобы выбрать цель.",
            reply_markup=get_goal_keyboard(),
        )
        return

    goal_name = GOAL_OPTIONS[button_text]

    # Получаем сохранённые данные
    user_data = await state.get_data()
    role_name = user_data.get("role_name")
    scenario_name = user_data.get("scenario_name")

    if not role_name or not scenario_name:
        await message.answer("⚠️ Что-то пошло не так. Начни заново: /start")
        await state.clear()
        return

    # Извлекаем промпты
    role_prompt = ROLES[role_name]["prompt"]
    scenario_context = SCENARIOS[scenario_name]["context"]
    goal_criteria = GOALS[goal_name]["criteria"]

    # Собираем системный промпт
    system_prompt = build_system_prompt(
        role_prompt=role_prompt,
        scenario_context=scenario_context,
        goal_criteria=goal_criteria,
    )

    # Инициализируем историю диалога
    user_id = message.from_user.id
    user_messages[user_id] = [
        {"role": "system", "content": system_prompt}
    ]

    # Сохраняем параметры игры
    user_game_data[user_id] = {
        "role_name": role_name,
        "scenario_name": scenario_name,
        "goal_name": goal_name,
    }

    logger.info(
        f"Пользователь {user_id} запустил игру: "
        f"роль={role_name}, сценарий={scenario_name}, цель={goal_name}"
    )

    await message.answer(
        "🎭 <b>Игра начинается!</b>\n\n"
        f"• Оппонент: <b>{role_name}</b>\n"
        f"• Сценарий: <b>{scenario_name}</b>\n"
        f"• Твоя цель: <b>{goal_name}</b>\n\n"
        "Сейчас оппонент представится и выдаст твою скрытую цель. Поехали! 🚀\n\n"
        "(В любой момент отправь /stop для завершения и анализа)",
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardRemove(),
    )

    # Запрашиваем первое сообщение от AI
    user_messages[user_id].append({
        "role": "user",
        "content": (
            "Начни игру. Представься как оппонент согласно сценарию и роли. "
            "Первым сообщением выдай пользователю его скрытую цель "
            "(в формате, указанном в инструкции). "
            "Затем сразу начни диалог своей первой репликой."
        )
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ai_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": ai_response})

    await message.answer(ai_response)
    await state.set_state(GameSetup.in_dialog)


@dp.message(StateFilter(GameSetup.in_dialog))
async def handle_dialog_message(message: types.Message, state: FSMContext):
    """Обработка сообщений ТОЛЬКО в режиме диалога."""
    user_id = message.from_user.id

    # Команда /stop в диалоге
    if message.text == "/stop":
        await finish_game(message, state)
        return

    # Проверяем, что диалог существует
    if user_id not in user_messages:
        await message.answer("⚠️ Диалог не найден. Начни заново: /start")
        await state.clear()
        return

    # Добавляем сообщение пользователя
    user_messages[user_id].append({"role": "user", "content": message.text})

    # Получаем ответ AI
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ai_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": ai_response})

    await message.answer(ai_response)


async def finish_game(message: types.Message, state: FSMContext):
    """Завершение игры и запрос анализа."""
    user_id = message.from_user.id

    if user_id not in user_messages:
        await message.answer("⚠️ Нет активного диалога для анализа.")
        await state.clear()
        return

    await message.answer("🛑 Диалог завершён. Запрашиваю анализ игры...")

    # Просим AI выдать анализ
    user_messages[user_id].append({
        "role": "user",
        "content": (
            "/stop. Выдай полный анализ игры согласно инструкции "
            "(раздел 5, Процедура тренировки)."
        )
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    analysis = await get_ai_response(user_messages[user_id])

    await message.answer(analysis)

    # Очищаем состояние и данные
    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    await message.answer(
        "🎉 Игра окончена! Хочешь попробовать снова? Отправь /start",
        reply_markup=types.ReplyKeyboardRemove(),
    )

    logger.info(f"Пользователь {user_id} завершил игру")


@dp.message(Command("help"))
async def help_command(message: types.Message):
    """Справка по боту."""
    help_text = (
        "🎯 <b>Business Negotiation Trainer</b>\n\n"
        "<b>Как играть:</b>\n"
        "1. /start — начать новую игру\n"
        "2. Выбери роль, сценарий и цель с помощью кнопок\n"
        "3. Получи скрытую цель и веди диалог\n"
        "4. /stop — завершить и получить анализ\n\n"
        "<b>Роли:</b> Дмитрий (Заказчик), Виктор (Топ-менеджер), Сергей (Конкурент)\n"
        "<b>Сценарии:</b> 10 типовых ситуаций\n"
        "<b>Цели:</b> отстоять границы, принять решение, удержать позицию\n\n"
        "Удачи в переговорах! 💼"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("stop"))
async def stop_anywhere(message: types.Message, state: FSMContext):
    """Обработка /stop в любом состоянии."""
    current_state = await state.get_state()
    user_id = message.from_user.id

    # Если мы в диалоге — завершаем с анализом
    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
        return

    # В остальных случаях просто сбрасываем
    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    await message.answer(
        "🔄 Всё сброшено. Начни заново: /start",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@dp.message()
async def unexpected_message(message: types.Message):
    """Любые другие сообщения."""
    await message.answer(
        "👋 Я тебя не понял. Отправь /start, чтобы начать игру, "
        "или /help для справки."
    )


async def main():
    # Инициализируем словари кнопок ОДИН раз
    init_options()
    logger.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
