from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings


class ThrottlingMiddleware(BaseMiddleware):
    """Не больше одного апдейта от чата за `throttle_interval_seconds`. Без этого
    пользователь мог бы, например, дёргать /watcher или /track_item в цикле и нагружать
    наши исходящие запросы к Steam и БД без всякого предела — это не про UX, а про то,
    чтобы один недобросовестный чат не мог посадить бота всем остальным.

    Простой in-memory словарь (не Redis) — специально: это защита процесса бота от
    злоупотребления его же собственными пользователями, синхронизация между процессами
    не нужна (worker.py вообще не принимает пользовательский ввод). Словарь растёт на
    один float на каждый уникальный chat_id за всё время, это не одна запись на сообщение —
    для масштаба этого проекта не критично."""

    def __init__(self, interval_seconds: float | None = None) -> None:
        self._interval = interval_seconds if interval_seconds is not None else settings.throttle_interval_seconds
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id: int | None = None
        if isinstance(event, Message) and event.chat is not None:
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat_id = event.message.chat.id

        if chat_id is not None:
            now = time.monotonic()
            last = self._last_seen.get(chat_id)
            if last is not None and now - last < self._interval:
                if isinstance(event, CallbackQuery):
                    await event.answer()  # чтобы у пользователя не крутились "часики" на кнопке
                return None
            self._last_seen[chat_id] = now

        return await handler(event, data)
