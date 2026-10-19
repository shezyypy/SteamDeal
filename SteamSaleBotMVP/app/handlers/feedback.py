from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.keyboards.inline import feedback_topic_kb, yes_no_photo_kb
from app.states.fsm_states import Feedback

router = Router(name="feedback")


def _who(message: types.Message) -> str:
    handle = f"@{message.from_user.username}" if message.from_user.username else "без username"
    return f"{message.from_user.full_name} ({handle})"


@router.message(Command("feedback"))
async def cmd_feedback(message: types.Message) -> None:
    await message.answer("✉️ О чём хотите написать?", reply_markup=feedback_topic_kb())


@router.callback_query(F.data.startswith("feedback_topic:"))
async def choose_topic(callback: types.CallbackQuery, state: FSMContext) -> None:
    topic = callback.data.split(":", 1)[1]
    prompt = (
        "🚫 Опишите проблему как можно подробнее — так мы решим её быстрее."
        if topic == "error"
        else "💡 Опишите вашу идею как можно подробнее — обдумаем и, может быть, воплотим в боте."
    )
    await callback.message.edit_text(prompt)
    await state.set_state(Feedback.waiting_text)
    await callback.answer()


@router.message(Feedback.waiting_text)
async def receive_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)

    if settings.admin_chat_id:
        await message.bot.send_message(
            settings.admin_chat_id,
            f"✉️ Новое сообщение от {_who(message)}:\n{message.text}",
        )

    await message.answer("Хотите приложить фотографию?", reply_markup=yes_no_photo_kb())
    await state.set_state(Feedback.waiting_photo_choice)


@router.callback_query(Feedback.waiting_photo_choice, F.data == "feedback_photo:yes")
async def ask_photo(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text("Пришлите фотографию 🌅")
    await state.set_state(Feedback.waiting_photo)
    await callback.answer()


@router.callback_query(Feedback.waiting_photo_choice, F.data == "feedback_photo:no")
async def skip_photo(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text("Спасибо! Учтём при следующем обновлении бота ❤️")
    await state.clear()
    await callback.answer()


@router.message(Feedback.waiting_photo, F.photo)
async def receive_photo(message: types.Message, state: FSMContext) -> None:
    if settings.admin_chat_id:
        await message.bot.send_photo(
            settings.admin_chat_id,
            message.photo[-1].file_id,
            caption=f"Фото от {_who(message)}",
        )

    await message.answer("Спасибо! Учтём при следующем обновлении бота ❤️")
    await state.clear()
