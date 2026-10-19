from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.db import repository
from app.db.base import session_scope
from app.db.models import SaleSnapshot
from app.services.steam_pricing import fetch_prices
from app.utils.links import steam_app_link

logger = logging.getLogger(__name__)


async def _record_snapshot(name: str, sale: dict) -> None:
    async with session_scope() as session:
        session.add(
            SaleSnapshot(
                appid=sale["appid"],
                name=name,
                price_original_cents=sale["price_original_cents"],
                price_final_cents=sale["price_final_cents"],
                discount_percent=sale["discount_percent"],
                currency=sale["currency"],
            )
        )
        await session.commit()


async def check_and_notify(bot: Bot, tracked_appids: list[int], region: str) -> None:
    """Раньше рассылка была полностью закомментирована в main.py — это её рабочая замена,
    вызывается планировщиком (app/scheduler/jobs.py) отдельно на каждый регион цен, а не
    вручную командой /send."""

    if not tracked_appids:
        return

    sales = await fetch_prices(tracked_appids, cc=region)
    logger.info(
        "[%s] проверено %d игр, скидка найдена у %d", region, len(tracked_appids), len(sales)
    )

    for sale in sales:
        appid = sale["appid"]
        chat_rows = await repository.chats_tracking_appid_in_region(appid, region)
        if not chat_rows:
            continue

        # Считаем ДО записи текущего снэпшота — иначе новая запись засчитает сама себя минимумом.
        record_low = await repository.is_all_time_low(appid, sale["price_final_cents"])

        games_by_chat: dict[int, str] | None = None
        name_for_snapshot = f"appid {appid}"

        for chat_id, min_discount in chat_rows:
            if sale["discount_percent"] < min_discount:
                continue
            if await repository.already_notified(chat_id, appid, sale["discount_percent"]):
                continue

            tracked = await repository.list_tracked_games(chat_id)
            games_by_chat = {g.appid: g.name for g in tracked}

            name = games_by_chat.get(appid, name_for_snapshot)
            name_for_snapshot = name
            price_orig = sale["price_original_cents"] / 100
            price_final = sale["price_final_cents"] / 100

            text = (
                f'🔥 {steam_app_link(appid, name)} подешевела!\n'
                f'Скидка <b>-{sale["discount_percent"]}%</b>: '
                f'<s>{price_orig:.0f}</s> → <b>{price_final:.0f} {sale["currency"]}</b>'
            )
            if record_low and await repository.is_chat_premium(chat_id):
                text += "\n🏆 Это самая низкая цена за всё время наблюдений!"

            try:
                await bot.send_message(chat_id, text)
                await repository.log_notification(chat_id, appid, sale["discount_percent"])
            except TelegramForbiddenError:
                # Чат заблокировал бота — помечаем и больше не тратим на него rate-limit
                # бюджет запросов к Steam (см. repository.appids_by_region/chats_tracking_*).
                logger.info("Чат %s заблокировал бота, помечаю как неактивный", chat_id)
                await repository.mark_chat_blocked(chat_id)
            except Exception:
                logger.exception("Не удалось отправить уведомление chat_id=%s appid=%s", chat_id, appid)

        await _record_snapshot(name_for_snapshot, sale)
