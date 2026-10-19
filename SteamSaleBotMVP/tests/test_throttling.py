import datetime as dt
from unittest.mock import AsyncMock

import pytest
from aiogram import Router
from aiogram.types import Chat, Message, Update, User

from app.middlewares.throttling import ThrottlingMiddleware


def _make_update(update_id: int, chat_id: int, text: str) -> Update:
    chat = Chat(id=chat_id, type="private")
    user = User(id=chat_id, is_bot=False, first_name="Test")
    message = Message(message_id=update_id, date=dt.datetime.now(), chat=chat, from_user=user, text=text)
    return Update(update_id=update_id, message=message)


@pytest.mark.asyncio
async def test_drops_second_message_within_interval_from_same_chat():
    middleware = ThrottlingMiddleware(interval_seconds=10)
    handler = AsyncMock()

    router = Router(name="dummy")
    router.message.outer_middleware(middleware)

    update1 = _make_update(1, 111, "/watcher")
    update2 = _make_update(2, 111, "/clear")

    await router.message.wrap_outer_middleware(handler, event=update1.message, data={})
    await router.message.wrap_outer_middleware(handler, event=update2.message, data={})

    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_does_not_throttle_different_chats():
    middleware = ThrottlingMiddleware(interval_seconds=10)
    handler = AsyncMock()

    router = Router(name="dummy")
    router.message.outer_middleware(middleware)

    update1 = _make_update(1, 111, "/watcher")
    update2 = _make_update(2, 222, "/watcher")

    await router.message.wrap_outer_middleware(handler, event=update1.message, data={})
    await router.message.wrap_outer_middleware(handler, event=update2.message, data={})

    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_allows_message_after_interval_elapses():
    middleware = ThrottlingMiddleware(interval_seconds=0.05)
    handler = AsyncMock()

    router = Router(name="dummy")
    router.message.outer_middleware(middleware)

    update1 = _make_update(1, 111, "/watcher")
    update2 = _make_update(2, 111, "/clear")

    await router.message.wrap_outer_middleware(handler, event=update1.message, data={})

    import asyncio

    await asyncio.sleep(0.08)
    await router.message.wrap_outer_middleware(handler, event=update2.message, data={})

    assert handler.await_count == 2
