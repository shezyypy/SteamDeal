import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from app.handlers import tracking
from app.middlewares.reset_state_on_command import ResetStateOnCommandMiddleware


def _make_update(update_id: int, message_id: int, text: str) -> Update:
    chat = Chat(id=111, type="private")
    user = User(id=111, is_bot=False, first_name="Test")
    message = Message(message_id=message_id, date=dt.datetime.now(), chat=chat, from_user=user, text=text)
    return Update(update_id=update_id, message=message)


@pytest.mark.asyncio
async def test_command_escapes_stuck_fsm_state_and_is_handled():
    # Регрессия: /watcher без аргумента переводит чат в состояние "жду название игры".
    # Раньше следующее сообщение — даже команда вроде /clear — ловилось этим состоянием
    # и уходило в поиск игры по названию "/clear", а не выполнялось как команда.
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(ResetStateOnCommandMiddleware())
    dp.include_router(tracking.router)

    bot = Bot(token="123456:fake-token-for-tests")

    with (
        patch("app.handlers.tracking.repository.list_tracked_games", AsyncMock(return_value=[])) as list_games,
        patch("app.services.steam_catalog.resolve_appid_by_name", AsyncMock()) as resolve_by_name,
        patch.object(Bot, "__call__", AsyncMock()),
    ):
        await dp.feed_update(bot, _make_update(1, 1, "/watcher"))

        state = dp.fsm.resolve_context(bot, chat_id=111, user_id=111)
        assert await state.get_state() is not None, "watcher должен был перевести чат в состояние ожидания"

        await dp.feed_update(bot, _make_update(2, 2, "/clear"))

        # Если бы баг остался, "/clear" ушёл бы в _resolve_and_track -> resolve_appid_by_name.
        resolve_by_name.assert_not_called()
        # А должен был отработать именно cmd_clear, который читает список через list_tracked_games.
        list_games.assert_awaited_once_with(111)

    assert await state.get_state() is None, "команда должна была сбросить зависшее состояние"

    await bot.session.close()
