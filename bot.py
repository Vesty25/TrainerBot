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


# === Вспомогательные функции для клавиатур ===

def build_role_keyboard():
    """Клавиатура выбора роли оппонента."""
    buttons = []
    for role_name in ROLES.keys():
        buttons.append([types.KeyboardButton(text=role_name)])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери роль оппонента",
    )


def build_scenario_keyboard():
    """Клавиатура выбора сценария."""
    buttons = []
    for scenario_name in SCENARIOS.keys():
        buttons.append([types.KeyboardButton(text=scenario_name)])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери сценарий",
    )


def build_goal_keyboard():
    """Клавиатура выбора цели."""
    buttons = []
    for goal_name in GOALS.keys():
        buttons.append([types.KeyboardButton(text=goal_name)])
    return types.ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери свою цель",
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
    for role_name, role_data in ROLES.items():
        roles_text += f"<b>{role_name}</b>\n{role_data['description']}\n\n"

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
    role_name = message.text

    if role_name not in ROLES:
        await message.answer(
            "Пожалуйста, выбери роль с помощью кнопок ниже.",
            reply_markup=build_role_keyboard(),
        )
        return

    await state.update_data(role_name=role_name)

    # Формируем текст с описанием сценариев
    scenarios_text = f"✅ <b>Роль оппонента:</b> {role_name}\n\n"
    scenarios_text += "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
    for name, data in SCENARIOS.items():
        context = data["context"].replace("Контекст: ", "")
        preview = context[:120].rsplit(".", 1)[0] + "."
        scenarios_text += f"<b>{name}</b>\n{preview}\n\n"

    await message.answer(
        scenarios_text,
        parse_mode="HTML",
        reply_markup=build_scenario_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_scenario)


@dp.message(GameSetup.waiting_for_scenario)
async def process_scenario(message: types.Message, state: FSMContext):
    """Обработка выбора сценария."""
    scenario_name = message.text

    if scenario_name not in SCENARIOS:
        await message.answer(
            "Пожалуйста, выбери сценарий с помощью кнопок ниже.",
            reply_markup=build_scenario_keyboard(),
        )
        return

    await state.update_data(scenario_name=scenario_name)

    # Формируем текст с описанием целей
    goals_text = f"✅ <b>Сценарий:</b> {scenario_name}\n\n"
    goals_text += "🎯 <b>Шаг 3/3: Выбери свою основную цель</b>\n\n"
    for goal_name, goal_data in GOALS.items():
        goals_text += f"<b>{goal_name}</b>\n{goal_data['description']}\n\n"

    await message.answer(
        goals_text,
        parse_mode="HTML",
        reply_markup=build_goal_keyboard(),
    )
    await state.set_state(GameSetup.waiting_for_goal)


@dp.message(GameSetup.waiting_for_goal)
async def process_goal(message: types.Message, state: FSMContext):
    """Обработка выбора цели и запуск игры."""
    goal_name = message.text

    if goal_name not in GOALS:
        await message.answer(
            "Пожалуйста, выбери цель с помощью кнопок ниже.",
            reply_markup=build_goal_keyboard(),
        )
        return

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
