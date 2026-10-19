"""Отдельный процесс: периодически проверяет скидки и шлёт уведомления.
Запускать отдельно от бота: python worker.py (см. README.md и docker-compose.yml)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import require, settings
from app.db.base import init_engine
from app.monitoring import init_sentry
from app.scheduler.jobs import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    require("bot_token", "database_url")
    init_sentry("worker")
    init_engine(settings.database_url)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    scheduler = build_scheduler(
        bot,
        settings.sale_poll_interval_minutes,
        settings.premium_poll_interval_minutes,
        settings.market_poll_interval_minutes,
        settings.digest_interval_hours,
    )
    scheduler.start()

    logger.info(
        "Воркер запущен. Обычные чаты — раз в %s мин., Premium — раз в %s мин., "
        "Market-алерты — раз в %s мин., дайджест — раз в %s ч.",
        settings.sale_poll_interval_minutes,
        settings.premium_poll_interval_minutes,
        settings.market_poll_interval_minutes,
        settings.digest_interval_hours,
    )
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
