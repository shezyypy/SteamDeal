from aiogram.dispatcher.filters.state import StatesGroup, State


class UserStates(StatesGroup):
    step1 = State()
    step2 = State()

    def get_state(self, data):
        state = data.get(self.__class__.__name__)
        return self.step1 if state is None else state


class UserPhoto(StatesGroup):
    photo1 = State()

    def get_state(self, data):
        state = data.get(self.__class__.__name__)
        return self.photo1 if state is None else state


class GameCheck(StatesGroup):
    name = State()
    id = State()
    name_id = State()
    base = State()

    def get_state(self, data):
        state = data.get(self.__class__.__name__)
        return self.name if state is None else state

class DeleteGame(StatesGroup):
    number = State()

    def get_state(self, data):
        state = data.get(self.__class__.__name__)
        return self.name if state is None else state