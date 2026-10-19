"""Точка входа бота: только принимает сообщения и отвечает. Проверку скидок и рассылку
делает отдельный процесс — worker.py (см. README.md, зачем это разделено на два процесса)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import require, settings
from app.db.base import init_engine
from app.handlers import admin, digest, feedback, market, premium, referral, start, tracking, wishlist
from app.middlewares.ensure_records import EnsureRecordsMiddleware
from app.middlewares.reset_state_on_command import ResetStateOnCommandMiddleware
from app.middlewares.throttling import ThrottlingMiddleware
from app.monitoring import init_sentry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_storage():
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)

    logger.warning(
        "REDIS_URL не задан в .env — используется MemoryStorage, состояния диалогов "
        "потеряются при перезапуске бота. Это ок для локальной разработки, но не для прода."
    )
    return MemoryStorage()


async def main() -> None:
    require("bot_token", "database_url")
    init_sentry("bot")
    init_engine(settings.database_url)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=build_storage())

    throttling = ThrottlingMiddleware()
    dp.message.outer_middleware(throttling)
    dp.callback_query.outer_middleware(throttling)

    dp.message.outer_middleware(ResetStateOnCommandMiddleware())
    dp.message.outer_middleware(EnsureRecordsMiddleware())
    dp.callback_query.outer_middleware(EnsureRecordsMiddleware())

    dp.include_router(start.router)
    dp.include_router(feedback.router)
    dp.include_router(tracking.router)
    dp.include_router(wishlist.router)
    dp.include_router(premium.router)
    dp.include_router(market.router)
    dp.include_router(admin.router)
    dp.include_router(referral.router)
    dp.include_router(digest.router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен, начинаю polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
