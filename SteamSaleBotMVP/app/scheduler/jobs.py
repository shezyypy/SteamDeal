from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import repository
from app.services.digest import send_daily_digest
from app.services.market_notifier import check_market_alerts
from app.services.notifier import check_and_notify

logger = logging.getLogger(__name__)


async def _run_check(bot: Bot, premium_only: bool) -> None:
    # Пишем heartbeat независимо от того, нашлось ли что проверять — иначе /admin_stats не
    # отличит "нечего слать" от "воркер вообще не запускается".
    await repository.record_heartbeat("check_sales_premium" if premium_only else "check_sales_free")

    region_to_appids = await repository.appids_by_region(premium_only)
    total = sum(len(appids) for appids in region_to_appids.values())
    logger.info(
        "Плановая проверка скидок (%s): %d игр в %d регионах",
        "premium" if premium_only else "обычная",
        total,
        len(region_to_appids),
    )

    for region, appids in region_to_appids.items():
        await check_and_notify(bot, appids, region)


def build_scheduler(
    bot: Bot,
    base_interval_minutes: int,
    premium_interval_minutes: int,
    market_interval_minutes: int,
    digest_interval_hours: int,
) -> AsyncIOScheduler:
    """Четыре независимых цикла: обычная проверка скидок, ускоренная (Premium), алерты по
    предметам Market (см. market_notifier.py: у Market API более жёсткие лимиты запросов,
    чем у store-api для игр) и дайджест топ-скидок для опт-ин чатов (+ сезонный баннер,
    см. app/services/digest.py)."""

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _run_check, "interval", args=[bot, False], minutes=base_interval_minutes, id="check_sales_free"
    )
    scheduler.add_job(
        _run_check, "interval", args=[bot, True], minutes=premium_interval_minutes, id="check_sales_premium"
    )
    scheduler.add_job(
        check_market_alerts, "interval", args=[bot], minutes=market_interval_minutes, id="check_market_alerts"
    )
    scheduler.add_job(
        send_daily_digest, "interval", args=[bot], hours=digest_interval_hours, id="send_digest"
    )

    return scheduler
