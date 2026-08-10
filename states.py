from aiogram.fsm.state import State, StatesGroup


class GameSetup(StatesGroup):
    """Состояния конструктора игры."""
    waiting_for_role = State()
    waiting_for_scenario = State()
    waiting_for_goal = State()
    waiting_for_ready = State()   # новое: ожидание нажатия "Готов"
    in_dialog = State()
