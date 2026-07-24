from aiogram.fsm.state import State, StatesGroup


class GameSetup(StatesGroup):
    """Состояния конструктора игры."""
    waiting_for_role = State()      # выбор роли оппонента
    waiting_for_scenario = State()  # выбор сценария
    waiting_for_goal = State()      # выбор цели пользователя
    in_dialog = State()             # активный диалог
