from aiogram.fsm.state import State, StatesGroup


class Feedback(StatesGroup):
    waiting_text = State()
    waiting_photo_choice = State()
    waiting_photo = State()


class Tracking(StatesGroup):
    waiting_game_input = State()


class Wishlist(StatesGroup):
    waiting_profile = State()


class MarketAlert(StatesGroup):
    waiting_link = State()
    waiting_direction = State()
    waiting_price = State()
