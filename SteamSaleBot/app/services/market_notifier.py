from __future__ import annotations

import logging

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.config import settings
from app.db import repository
from app.services.rate_limit import RateLimiter
from app.services.steam_market import SteamMarketUnavailable, fetch_market_price_cents
from app.utils.links import market_item_link

logger = logging.getLogger(__name__)

# Market API лимитирует запросы жёстче, чем store-api — отдельный, более консервативный
# RateLimiter (не Semaphore: важна реальная скорость запросов, а не параллелизм).
_rate_limiter = RateLimiter(settings.steam_market_rate_limit_per_minute, period_seconds=60)


async def check_market_alerts(bot: Bot) -> None:
    # Пишем heartbeat независимо от того, есть ли что проверять — иначе /admin_stats не
    # отличит "нечего слать" от "воркер вообще не запускается".
    await repository.record_heartbeat("check_market_alerts")

    alerts = await repository.all_market_alerts()
    if not alerts:
        return

    logger.info("Проверка алертов по предметам Market: %d активных", len(alerts))

    # Один и тот же предмет+регион может встречаться в нескольких алертах (below и above
    # разом, или несколько чатов) — не дублируем запрос к жёстко лимитированному API.
    price_cache: dict[tuple[int, str, str], int | None] = {}

    async with aiohttp.ClientSession() as session:

        async def _get_price(appid: int, market_hash_name: str, region: str) -> int | None:
            key = (appid, market_hash_name, region)
            if key in price_cache:
                return price_cache[key]

            await _rate_limiter.acquire()
            try:
                price = await fetch_market_price_cents(session, appid, market_hash_name, region)
            except SteamMarketUnavailable as exc:
                logger.warning("Не удалось получить цену %s (appid=%s): %s", market_hash_name, appid, exc)
                price = None

            price_cache[key] = price
            return price

        for alert in alerts:
            price_cents = await _get_price(alert["appid"], alert["market_hash_name"], alert["region"])
            if price_cents is None:
                continue

            triggered = (
                alert["direction"] == "below" and price_cents <= alert["threshold_cents"]
            ) or (alert["direction"] == "above" and price_cents >= alert["threshold_cents"])
            if not triggered:
                continue

            arrow = "упала ниже" if alert["direction"] == "below" else "выросла выше"
            name = market_item_link(alert["appid"], alert["market_hash_name"])
            try:
                await bot.send_message(
                    alert["chat_id"],
                    f"📊 Цена предмета {name} {arrow} {alert['threshold_cents'] / 100:.2f} — "
                    f"сейчас {price_cents / 100:.2f}.\nАлерт сработал и снят с отслеживания.",
                )
            except TelegramForbiddenError:
                logger.info("Чат %s заблокировал бота, помечаю как неактивный", alert["chat_id"])
                await repository.mark_chat_blocked(alert["chat_id"])
            except Exception:
                logger.exception("Не удалось отправить алерт chat_id=%s item=%s", alert["chat_id"], name)

            await repository.delete_market_alert(alert["id"])
