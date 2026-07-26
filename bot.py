import asyncio
import logging

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


def build_role_text():
    """Текст с описанием ролей."""
    text = "🎭 <b>Шаг 1/3: Выбери роль оппонента</b>\n\n"
    text += "Напиши в ответе номер варианта (1, 2 или 3):\n\n"
    for i, (role_name, role_data) in enumerate(ROLES.items(), start=1):
        text += f"<b>Вариант {i}: {role_name}</b>\n{role_data['description']}\n\n"
    return text


def build_scenario_text(selected_role: str):
    """Текст с описанием сценариев."""
    text = f"✅ <b>Роль оппонента:</b> {selected_role}\n\n"
    text += "📋 <b>Шаг 2/3: Выбери сценарий</b>\n\n"
    text += "Напиши в ответе номер варианта (от 1 до 10):\n\n"
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
    text += "Напиши в ответе номер варианта (1, 2 или 3):\n\n"
    for i, (goal_name, goal_data) in enumerate(GOALS.items(), start=1):
        text += f"<b>Вариант {i}: {goal_name}</b>\n{goal_data['description']}\n\n"
    return text


def get_option_by_number(options_dict: dict, number_str: str):
    """Получает значение из словаря по номеру (1-based)."""
    try:
        num = int(number_str.strip())
        keys = list(options_dict.keys())
        if 1 <= num <= len(keys):
            return keys[num - 1]
    except ValueError:
        pass
    return None


# === Веб-сервер-заглушка для Render ===

async def health_check(request):
    """Заглушка: отвечает 'OK' на любой запрос, чтобы Render видел открытый порт."""
    return web.Response(text="OK")


async def run_web_server():
    """Запускает веб-сервер на порту 10000."""
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    logger.info("Health check server started on port 10000")


# === Обработчики ===

@dp.message(Command("start"))
async def start_command(message: types.Message, state: FSMContext):
    """Начало — Шаг 1: выбор роли оппонента."""
    user_id = message.from_user.id

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    await message.answer(
        "👋 <b>Привет!</b> Я — тренажёр деловых переговоров.\n\n"
        "Сейчас мы настроим игру. На каждом шаге просто напиши номер варианта.\n\n"
        + build_role_text(),
        parse_mode="HTML",
    )
    await state.set_state(GameSetup.waiting_for_role)


@dp.message(StateFilter(GameSetup.waiting_for_role))
async def process_role(message: types.Message, state: FSMContext):
    """Обработка выбора роли."""
    role_name = get_option_by_number(ROLES, message.text)

    if role_name is None:
        await message.answer(
            "❌ Пожалуйста, напиши номер варианта (1, 2 или 3).\n\n"
            + build_role_text(),
            parse_mode="HTML",
        )
        return

    await state.update_data(role_name=role_name)
    logger.info(f"Пользователь {message.from_user.id} выбрал роль: {role_name}")

    await message.answer(
        build_scenario_text(role_name),
        parse_mode="HTML",
    )
    await state.set_state(GameSetup.waiting_for_scenario)


@dp.message(StateFilter(GameSetup.waiting_for_scenario))
async def process_scenario(message: types.Message, state: FSMContext):
    """Обработка выбора сценария."""
    scenario_name = get_option_by_number(SCENARIOS, message.text)

    if scenario_name is None:
        await message.answer(
            "❌ Пожалуйста, напиши номер варианта (от 1 до 10).\n\n"
            + build_scenario_text("..."),
            parse_mode="HTML",
        )
        return

    await state.update_data(scenario_name=scenario_name)
    logger.info(f"Пользователь {message.from_user.id} выбрал сценарий: {scenario_name}")

    await message.answer(
        build_goal_text(scenario_name),
        parse_mode="HTML",
    )
    await state.set_state(GameSetup.waiting_for_goal)


@dp.message(StateFilter(GameSetup.waiting_for_goal))
async def process_goal(message: types.Message, state: FSMContext):
    """Обработка выбора цели и запуск игры."""
    goal_name = get_option_by_number(GOALS, message.text)

    if goal_name is None:
        await message.answer(
            "❌ Пожалуйста, напиши номер варианта (1, 2 или 3).\n\n"
            + build_goal_text("..."),
            parse_mode="HTML",
        )
        return

    user_data = await state.get_data()
    role_name = user_data.get("role_name")
    scenario_name = user_data.get("scenario_name")

    if not role_name or not scenario_name:
        await message.answer("⚠️ Что-то пошло не так. Начни заново: /start")
        await state.clear()
        return

    role_prompt = ROLES[role_name]["prompt"]
    scenario_context = SCENARIOS[scenario_name]["context"]
    goal_criteria = GOALS[goal_name]["criteria"]

    system_prompt = build_system_prompt(
        role_prompt=role_prompt,
        scenario_context=scenario_context,
        goal_criteria=goal_criteria,
    )

    user_id = message.from_user.id
    user_messages[user_id] = [
        {"role": "system", "content": system_prompt}
    ]

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
        "Сейчас оппонент начнёт диалог. Поехали! 🚀\n\n"
        "(В любой момент отправь /stop для завершения и анализа)",
        parse_mode="HTML",
    )

    user_messages[user_id].append({
        "role": "user",
        "content": "Начни диалог своей первой репликой согласно сценарию и роли."
    })

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    ai_response = await get_ai_response(user_messages[user_id])
    user_messages[user_id].append({"role": "assistant", "content": ai_response})

    await message.answer(ai_response)
    await state.set_state(GameSetup.in_dialog)


@dp.message(StateFilter(GameSetup.in_dialog))
async def handle_dialog_message(message: types.Message, state: FSMContext):
    """Обработка сообщений в режиме диалога."""
    user_id = message.from_user.id

    if message.text == "/stop":
        await finish_game(message, state)
        return

    if user_id not in user_messages:
        await message.answer("⚠️ Диалог не найден. Начни заново: /start")
        await state.clear()
        return

    user_messages[user_id].append({"role": "user", "content": message.text})

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

    await message.answer("🎉 Игра окончена! Хочешь попробовать снова? Отправь /start")
    logger.info(f"Пользователь {user_id} завершил игру")


@dp.message(Command("help"))
async def help_command(message: types.Message):
    help_text = (
        "🎯 <b>Business Negotiation Trainer</b>\n\n"
        "<b>Как играть:</b>\n"
        "1. /start — начать новую игру\n"
        "2. Выбери роль, сценарий и цель (напиши номер варианта)\n"
        "3. Веди диалог с ИИ-оппонентом\n"
        "4. /stop — завершить и получить анализ\n\n"
        "Удачи в переговорах! 💼"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("stop"))
async def stop_anywhere(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    user_id = message.from_user.id

    if current_state == GameSetup.in_dialog.state:
        await finish_game(message, state)
        return

    await state.clear()
    if user_id in user_messages:
        del user_messages[user_id]
    if user_id in user_game_data:
        del user_game_data[user_id]

    await message.answer("🔄 Всё сброшено. Начни заново: /start")


@dp.message()
async def unexpected_message(message: types.Message):
    await message.answer("👋 Отправь /start, чтобы начать игру, или /help для справки.")


async def main():
    logger.info("Бот запускается...")
    # Запускаем веб-сервер и бота параллельно
    await asyncio.gather(
        run_web_server(),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
