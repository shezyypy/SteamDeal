"""Ручная выдача/отзыв Premium для тестирования — в обход оплаты Stars.
Специально НЕ команда бота: если бы такое можно было вызвать в чате, любой пользователь
мог бы выдать себе Premium сам. Запускать только руками, с доступом к .env/БД.

Использование:
    python scripts/grant_premium.py <chat_id> [дней]      # выдать Premium
    python scripts/grant_premium.py <chat_id> revoke        # отозвать Premium

chat_id — для личных чатов это тот же самый Telegram user id (ADMIN_CHAT_ID и т.п.).
Чат должен быть уже известен боту — то есть человек хотя бы раз написал /start.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

# Скрипт лежит в scripts/, а не в корне проекта — без этого "from app..." не находится,
# т.к. python добавляет в sys.path только папку самого скрипта, а не корень проекта.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import require, settings  # noqa: E402
from app.db import repository  # noqa: E402
from app.db.base import init_engine  # noqa: E402


async def main(chat_id: int, days: int | None) -> None:
    require("database_url")
    init_engine(settings.database_url)

    chat = await repository.get_chat(chat_id)
    if chat is None:
        print(f"Чат {chat_id} ещё не известен боту — напишите боту /start хотя бы раз, затем повторите.")
        return

    if days is None:
        await repository.grant_premium(chat_id, dt.datetime.now(dt.timezone.utc))
        print(f"Premium для чата {chat_id} отозван.")
        return

    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    await repository.grant_premium(chat_id, until)
    print(f"Premium для чата {chat_id} выдан до {until.strftime('%d.%m.%Y')}.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    arg_chat_id = int(sys.argv[1])
    arg_days: int | None
    if len(sys.argv) > 2 and sys.argv[2] == "revoke":
        arg_days = None
    else:
        arg_days = int(sys.argv[2]) if len(sys.argv) > 2 else settings.premium_days

    asyncio.run(main(arg_chat_id, arg_days))
