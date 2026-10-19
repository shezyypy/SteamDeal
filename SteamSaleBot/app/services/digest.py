from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.config import settings
from app.db import repository
from app.utils.links import steam_app_link

logger = logging.getLogger(__name__)

_SEASONAL_ALERT_JOB_NAME = "seasonal_alert_sent"
_SEASONAL_ALERT_MIN_GAP_DAYS = 7  # не напоминать о той же распродаже каждый день 2 недели подряд


async def _seasonal_banner() -> str | None:
    """Если скидка сейчас одновременно у заметной доли отслеживаемых игр — это статистический
    сигнал крупной сторонней распродажи (Steam не даёт официального способа узнать даты
    сезонных распродаж заранее), а не просто "у нескольких игр совпала скидка". Используем уже
    собранные данные (sale_snapshots), не заводя отдельный скрейпинг специально под это."""
    total = await repository.count_distinct_tracked_appids()
    if total == 0:
        return None

    discounted = await repository.count_distinct_discounted_appids(hours=24)
    if discounted / total < settings.seasonal_alert_threshold:
        return None

    heartbeats = await repository.get_heartbeats()
    last_alert = heartbeats.get(_SEASONAL_ALERT_JOB_NAME)
    if last_alert and (dt.datetime.now(dt.timezone.utc) - last_alert).days < _SEASONAL_ALERT_MIN_GAP_DAYS:
        return None

    await repository.record_heartbeat(_SEASONAL_ALERT_JOB_NAME)
    return (
        "🎉 <b>Похоже, в Steam началась крупная распродажа!</b>\n"
        f"Сейчас скидка у {discounted} из {total} игр, которые отслеживают пользователи бота — "
        "самое время заглянуть в /list.\n\n"
    )


async def send_daily_digest(bot: Bot) -> None:
    # Пишем heartbeat независимо от того, есть ли что слать — иначе /admin_stats не отличит
    # "нечего слать" от "джоба вообще не запускается".
    await repository.record_heartbeat("send_digest")

    top = await repository.top_discounts_recent(hours=settings.digest_interval_hours, limit=10)
    seasonal_banner = await _seasonal_banner()

    if not top and not seasonal_banner:
        return

    lines = [
        f"{i}. {steam_app_link(s.appid, s.name)} — <b>-{s.discount_percent}%</b> "
        f"({s.price_final_cents / 100:.0f} {s.currency})"
        for i, s in enumerate(top, start=1)
    ]
    text = (seasonal_banner or "") + "🔥 <b>Топ скидок среди отслеживаемых игр:</b>\n" + "\n".join(lines)
    if not lines:
        text = seasonal_banner or ""

    chat_ids = await repository.chats_with_digest_enabled()
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
            try:
                await repository.create_miniapp_event(
                    chat_id,
                    "digest",
                    "🔥 Дайджест скидок",
                    "Топ скидок среди отслеживаемых игр",
                )
            except Exception:
                logger.exception("Не удалось записать дайджест в Mini App chat_id=%s", chat_id)
        except TelegramForbiddenError:
            logger.info("Чат %s заблокировал бота, помечаю как неактивный", chat_id)
            await repository.mark_chat_blocked(chat_id)
        except Exception:
            logger.exception("Не удалось отправить дайджест chat_id=%s", chat_id)
