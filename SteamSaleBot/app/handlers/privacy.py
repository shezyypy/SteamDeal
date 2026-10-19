from aiogram import Router, types
from aiogram.filters import Command

from app.db import repository


router = Router(name="privacy")


@router.message(Command("delete_data"))
async def cmd_delete_data(message: types.Message) -> None:
    """Удаляет данные только личного чата, не затрагивая общие группы."""
    if message.chat.type != "private":
        await message.answer(
            "Эта команда удаляет только персональные данные из личного чата. "
            "Общий список группы должен удалить администратор чата отдельно."
        )
        return
    deleted = await repository.delete_private_user_data(message.chat.id)
    if deleted:
        await message.answer("✅ Персональные данные, список игр и настройки удалены.")
    else:
        await message.answer("Персональных данных для удаления не найдено.")
