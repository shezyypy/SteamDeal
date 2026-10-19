from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class ResetStateOnCommandMiddleware(BaseMiddleware):
    """Если пользователь застрял в FSM-состоянии (например, бот ждёт название игры после
    /watcher или профиль после /import_wishlist) и присылает другую команду — сбрасываем
    состояние ДО того, как оно "съест" эту команду как обычный текстовый ответ на
    предыдущий шаг. Без этого, например, /clear во время незавершённого /watcher был бы
    воспринят как название игры для поиска, а не как команда."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and (event.text or "").startswith("/"):
            state = data.get("state")
            if state is not None and data.get("raw_state") is not None:
                await state.clear()
                # Фильтры состояний (State.__call__) сверяются с data["raw_state"], который
                # FSMContextMiddleware вычисляет один раз ДО того, как эта миддлварь успеет
                # отработать — просто вызвать state.clear() недостаточно, иначе хендлер,
                # завязанный на старое состояние, всё равно совпадёт для ЭТОГО события.
                data["raw_state"] = None

        return await handler(event, data)
