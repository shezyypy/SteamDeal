from __future__ import annotations

import asyncio
import logging

import aiohttp

from app.config import settings
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

# Общий на все вызовы в процессе — ограничивает реальную скорость запросов, а не только
# сколько их летит одновременно (см. app/services/rate_limit.py, почему это разные вещи).
_rate_limiter = RateLimiter(settings.steam_pricing_rate_limit_per_minute, period_seconds=60)


async def fetch_price(session: aiohttp.ClientSession, appid: int, cc: str = "ru") -> dict | None:
    await _rate_limiter.acquire()

    params = {"appids": str(appid), "cc": cc, "filters": "price_overview"}
    async with session.get(_APPDETAILS_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status == 429:
            logger.warning("Steam appdetails ответил 429 (rate limit) для appid=%s", appid)
            return None
        if resp.status != 200:
            return None
        payload = await resp.json()

    entry = payload.get(str(appid))
    if not entry or not entry.get("success"):
        return None

    # Steam иногда отдаёт "success": true, "data": [] (пустой список вместо словаря) —
    # например, если игра недоступна в конкретном регионе (cc). Это не ошибка сети,
    # просто "нет данных о цене", а не структура с price_overview.
    data = entry.get("data")
    if not isinstance(data, dict):
        return None

    overview = data.get("price_overview")
    if not overview or overview["initial"] == overview["final"]:
        return None

    return {
        "appid": appid,
        "price_original_cents": overview["initial"],
        "price_final_cents": overview["final"],
        "discount_percent": overview["discount_percent"],
        "currency": overview["currency"],
    }


async def fetch_prices(appids: list[int], cc: str = "ru") -> list[dict]:
    """
    ВАЖНО (см. разбор Goodparser/test3.py-test4.py): appdetails официально рассчитан на один
    appid за запрос. Пачки через запятую (?appids=1,2,3) в сообществе разработчиков считаются
    ненадёжными — Steam может тихо вернуть данные не по всем id пачки. Поэтому здесь каждый
    appid запрашивается отдельно; реальная скорость запросов ограничена общим RateLimiter
    (а не только тем, сколько корутин работает параллельно) — при росте числа отслеживаемых
    игр цикл опроса просто займёт больше времени, а не пробьёт лимит.
    """
    results: list[dict] = []

    async with aiohttp.ClientSession() as session:

        async def _worker(appid: int) -> None:
            try:
                price = await fetch_price(session, appid, cc)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                price = None
            except Exception:
                # Steam изредка отдаёт неожиданную форму ответа для конкретной игры —
                # одна такая игра не должна ронять всю проверку скидок остальных.
                logger.exception("Не удалось разобрать ответ appdetails для appid=%s", appid)
                price = None
            if price:
                results.append(price)

        await asyncio.gather(*(_worker(appid) for appid in appids))

    return results
