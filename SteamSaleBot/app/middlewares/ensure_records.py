from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.db import repository


class EnsureRecordsMiddleware(BaseMiddleware):
    """Перед тем как отдать апдейт хендлеру, гарантирует, что чат (личка/группа) и
    пользователь уже есть в БД. Без этого пришлось бы дублировать get_or_create_chat/
    get_or_create_user в каждом хендрере, который что-то пишет в tracked_games."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = event.message if isinstance(event, CallbackQuery) else event

        if isinstance(message, Message):
            if message.chat is not None:
                await repository.get_or_create_chat(message.chat.id, message.chat.type, message.chat.title)
            if message.from_user is not None:
                await repository.get_or_create_user(message.from_user.id, message.from_user.username)

        return await handler(event, data)
