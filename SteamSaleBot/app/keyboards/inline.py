from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

WATCHER_BUTTON = "🔍 Отслеживать игру"
LIST_BUTTON = "📋 Мой список"
WISHLIST_BUTTON = "⭐ Импорт вишлиста"


def main_menu_kb(webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=WATCHER_BUTTON), KeyboardButton(text=LIST_BUTTON))
    builder.row(KeyboardButton(text=WISHLIST_BUTTON))
    if webapp_url:
        builder.row(KeyboardButton(text="🖥 Открыть Mini App", web_app=WebAppInfo(url=webapp_url)))
    return builder.as_markup(resize_keyboard=True)


def feedback_topic_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚫 Проблема", callback_data="feedback_topic:error")
    builder.button(text="💡 Идея", callback_data="feedback_topic:idea")
    return builder.as_markup()


def yes_no_photo_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да", callback_data="feedback_photo:yes")
    builder.button(text="Нет", callback_data="feedback_photo:no")
    return builder.as_markup()


def premium_offer_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Оформить Premium", callback_data="premium:buy")
    builder.button(text="🎁 Попробовать бесплатно", callback_data="premium:trial")
    builder.adjust(1)
    return builder.as_markup()


def clear_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, очистить всё", callback_data="clear:confirm")
    builder.button(text="Отмена", callback_data="clear:cancel")
    return builder.as_markup()
