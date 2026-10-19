from __future__ import annotations

import logging

from aiogram import Bot

from app.config import settings
from app.db import repository

logger = logging.getLogger(__name__)


async def reward_referral_on_first_game(bot: Bot, chat_id: int, games_before_add: int) -> None:
    """Награждает реферера, только если у chat_id есть непрошенный referrer и это была
    первая добавленная игра в чате (games_before_add == 0) — так нельзя накрутить рефералов
    пустыми /start без реального использования бота."""
    if games_before_add > 0:
        return

    referrer_chat_id = await repository.try_reward_referral(chat_id, settings.referral_reward_days)
    if referrer_chat_id is None:
        return

    try:
        await bot.send_message(
            referrer_chat_id,
            f"🎉 Друг присоединился по вашей ссылке и начал пользоваться ботом — "
            f"начислено <b>+{settings.referral_reward_days} дней Premium</b>!",
        )
    except Exception:
        logger.exception("Не удалось уведомить реферера chat_id=%s", referrer_chat_id)
