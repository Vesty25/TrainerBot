import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
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

# Прокси для Telegram API (только если PROXY_URL задан)
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

# Сопоставление кнопок и значений
ROLE_BUTTONS = {}      # "Вариант 1" -> "Требовательный Заказчик (Дмитрий)"
SCENARIO_BUTTONS = {}  # "Вариант 1" -> "Срыв сроков"
GOAL_BUTTONS = {}      # "Вариант 1" -> "Отстоять границы и сказать «нет»"


def build_role_keyboard():
    """Клавиатура выбора роли — Вариант 1, Вариант 2, Вариант 3."""
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
        input_field_placeholder="Выбери вариант роли",
    )


def build_scenario_keyboard():
    """Клавиатура выбора сценария — Вариант 1..10."""
    global SCENARIO_BUTTONS
    SCENARIO_BUTTONS = {}
    buttons = []
    for i, scenario_name in enumerate(SCENARIOS.keys(), start=1):
        button_text = f"Вариант {i}"
        SCENARIO_BUTTONS[button_text] = scenario_name
        buttons.append([types.KeyboardButton(text=button_text)])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери вариант сценария",
    )


def build_goal_keyboard():
    """Клавиатура выбора цели — Вариант 1, Вариант 2, Вариант 3."""
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
        input_field_placeholder="Выбери вариант цели",
    )


# === Обработчики ===

@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Начало — Шаг 1: выбор роли оппонента."""
    user_id = message.from_user.id
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    # Формируем текст с описанием ролей
    roles_text = "🎭 <b>Шаг 1/3: Выбери роль оппонента</b>\n\n"
    for i, (role_name, role_data) in enumerate(ROLES.items(), start=1):
        roles_text += f"<b>Вариант {i}: {role_name}</b>\n{role_data['description']}\n\n"

    await message.answer(
        "👋 <b>Привет!</b> Я — тренажёр деловых переговоров с игровой механикой скрытых целей.\n\n"
        "Сейчас мы настроим игру.\n\n"
        + roles_text,
        parse_mode="HTML",
        reply_markup=build_role_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_role)


@dp.message(GameSetup.waiting_for_role)
async def process_role(message: types.Message, state: FSMContext):
    """Обработка выбора роли."""
    button_text = message.text

    if button_text not in ROLE_BUTTONS:
        await message.answer(
            "Пожалуйста, выбери вариант с помощью кнопок ниже.",
            reply_markup=build_role_keyboard(),
        )
        return

    role_name = ROLE_BUTTONS[button_text]
    await state.update_data(role_name=role_name)

    # Формируем текст с описанием сценариев
    scenarios_text = f"✅ <b>Роль оппонента:</b> {role_name}\n\n"
    scenarios_text += "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
    for i, (name, data) in enumerate(SCENARIOS.items(), start=1):
        context = data["context"].replace("Контекст: ", "")
        preview = context[:150].rsplit(".", 1)[0] + "."
        scenarios_text += f"<b>Вариант {i}: {name}</b>\n{preview}\n\n"

    await message.answer(
        scenarios_text,
        parse_mode="HTML",
        reply_markup=build_scenario_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_scenario)


@dp.message(GameSetup.waiting_for_scenario)
async def process_scenario(message: types.Message, state: FSMContext):
    """Обработка выбора сценария."""
    button_text = message.text

    if button_text not in SCENARIO_BUTTONS:
        await message.answer(
            "Пожалуйста, выбери вариант с помощью кнопок ниже.",
            reply_markup=build_scenario_keyboard(),
        )
        return

    scenario_name = SCENARIO_BUTTONS[button_text]
    await state.update_data(scenario_name=scenario_name)

    # Формируем текст с описанием целей
    goals_text = f"✅ <b>Сценарий:</b> {scenario_name}\n\n"
    goals_text += "🎯 <b>Шаг 3/3: Выбери свою основную цель</b>\n\n"
    for i, (goal_name, goal_data) in enumerate(GOALS.items(), start=1):
        goals_text += f"<b>Вариант {i}: {goal_name}</b>\n{goal_data['description']}\n\n"

    await message.answer(
        goals_text,
        parse_mode="HTML",
        reply_markup=build_goal_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_goal)


@dp.message(GameSetup.waiting_for_goal)
async def process_goal(message: types.Message, state: FSMContext):
    """Обработка выбора цели и запуск игры."""
    button_text = message.text

    if button_text not in GOAL_BUTTONS:
        await message.answer(
            "Пожалуйста, выбери вариант с помощью кнопок ниже.",
            reply_markup=build_goal_keyboard(),
        )
        return

    goal_name = GOAL_BUTTONS[button_text]

    # Получаем все сохранённые данные
    user_data = await state.get_data()
    role_name = user_data["role_name"]
    scenario_name = user_data["scenario_name"]

    # Извлекаем нужные промпты
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


@dp.message(GameSetup.in_dialog)
async def handle_dialog_message(message: types.Message, state: FSMContext):
    """Обработка сообщений в режиме диалога."""
    user_id = message.from_user.id

    if message.text == "/stop":
        await finish_game(message, state)
        return

    if user_id not in user_messages:
        await message.answer("Диалог не найден. Начни заново: /start")
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
        "🎉 Игра окончена! Хочешь попробовать снова с другими настройками? Отправь /start",
        reply_markup=types.ReplyKeyboardRemove(),
    )

    logger.info(f"Пользователь {user_id} завершил игру")


@dp.message(Command("help"))
async def help_command(message: types.Message):
    """Справка по боту."""
    help_text = (
        "🎯 <b>Business Negotiation Trainer</b> — тренажёр деловых переговоров с ИИ-оппонентом.\n\n"
        "<b>Как играть:</b>\n"
        "1. Отправь /start для начала\n"
        "2. Пройди 3 шага настройки:\n"
        "   • Выбери роль оппонента (характер и тактику)\n"
        "   • Выбери сценарий переговоров\n"
        "   • Выбери свою основную цель\n"
        "3. После запуска ты получишь <b>скрытую цель</b> — дополнительную задачу, "
        "которую нужно выполнить незаметно для оппонента\n"
        "4. Веди диалог как в реальных переговорах\n"
        "5. Отправь /stop для завершения и <b>полного анализа</b>\n\n"
        "<b>Что в анализе:</b>\n"
        "• Достиг ли ты основной цели\n"
        "• Достиг ли ты скрытой цели (и раскрыл ли её оппонент)\n"
        "• Какую скрытую цель преследовал оппонент\n"
        "• Ключевой момент переговоров\n"
        "• Твои точки уязвимости\n"
        "• Рекомендации по улучшению\n\n"
        "<b>Роли оппонента:</b>\n"
        "• <b>Дмитрий</b> — требовательный Заказчик, давит статусом\n"
        "• <b>Виктор</b> — стратегический Топ-менеджер, задаёт неудобные вопросы\n"
        "• <b>Сергей</b> — конкурирующий РП, переводит стрелки\n\n"
        "<b>Сценарии:</b> срыв сроков, провал на проде, перехват ресурсов, "
        "технический долг, обвинение в некомпетентности и другие (всего 10)\n\n"
        "<b>Цели:</b> отстоять границы, принять решение, удержать позицию под давлением\n\n"
        "Удачи в переговорах! 💼"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("stop"))
async def stop_anywhere(message: types.Message, state: FSMContext):
    """Обработка /stop в любом состоянии."""
    current_state = await state.get_state()
    user_id = message.from_user.id

    if current_state is not None:
        await state.clear()
        if user_id in user_messages:
            del user_messages[user_id]
        if user_id in user_game_data:
            del user_game_data[user_id]
        await message.answer(
            "Сброшено. Начни заново: /start",
            reply_markup=types.ReplyKeyboardRemove(),
        )
    else:
        await message.answer("Нет активной игры. Отправь /start")


@dp.message()
async def unexpected_message(message: types.Message):
    """Неожиданные сообщения."""
    await message.answer(
        "Я тебя не понял. Отправь /start, чтобы начать игру."
    )


async def main():
    logger.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
