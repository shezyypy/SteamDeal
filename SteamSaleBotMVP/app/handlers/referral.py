from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command

from app.config import settings

router = Router(name="referral")


@router.message(Command("referral"))
async def cmd_referral(message: types.Message) -> None:
    if message.chat.type != "private":
        await message.answer("Реферальные ссылки работают только в личных сообщениях с ботом.")
        return

    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref{message.chat.id}"

    await message.answer(
        "👥 <b>Приглашай друзей</b>\n"
        f"За каждого друга, который перейдёт по ссылке и добавит хотя бы одну игру в "
        f"отслеживание, вам начислится <b>+{settings.referral_reward_days} дней Premium</b>.\n\n"
        f"Ваша ссылка:\n{link}"
    )
