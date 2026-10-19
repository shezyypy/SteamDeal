from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from app.db import repository

router = Router(name="digest")


@router.message(Command("digest"))
async def cmd_digest(message: types.Message, command: CommandObject) -> None:
    args = (command.args or "").strip().lower()

    if args not in ("on", "off"):
        chat = await repository.get_chat(message.chat.id)
        current = "включён ✅" if (chat and chat.digest_enabled) else "выключен"
        await message.answer(
            f"📰 Дайджест топ-скидок среди отслеживаемых игр сейчас {current}.\n"
            "Включить — /digest on, выключить — /digest off."
        )
        return

    await repository.set_digest_enabled(message.chat.id, args == "on")
    if args == "on":
        await message.answer(
            "✅ Буду присылать дайджест топ-скидок примерно раз в сутки "
            "(плюс отдельно предупрежу, если начнётся крупная сезонная распродажа)."
        )
    else:
        await message.answer("Дайджест выключен — можно включить обратно в любой момент: /digest on")
