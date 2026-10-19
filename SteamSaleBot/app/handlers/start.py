from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from app.config import settings
from app.db import repository
from app.keyboards.inline import main_menu_kb

router = Router(name="start")

_REFERRAL_PREFIX = "ref"

PRIVATE_WELCOME = (
    "👋 <b>Привет!</b> Я слежу за скидками в Steam и как только на игру из твоего списка "
    "упадёт цена — сразу пришлю сюда уведомление.\n\n"
    "<b>С чего начать:</b>\n"
    "🔍 /watcher — добавить игру (по названию, ссылке на Steam или ID)\n"
    "⭐ /import_wishlist — импортировать весь вишлист Steam разом\n"
    "📋 /list — посмотреть список отслеживания\n\n"
    "Не уверены, нужен ли Premium — попробуйте бесплатно: /trial\n\n"
    "Полный список команд — /help"
)

GROUP_WELCOME = (
    "👋 <b>Привет, всем!</b> Теперь я слежу за скидками в Steam для всего этого чата — "
    "список общий, уведомления тоже придут сюда.\n\n"
    "🔍 <code>/watcher Hollow Knight</code> — добавить игру\n"
    "⭐ <code>/import_wishlist ссылка_на_профиль</code> — импортировать вишлист целиком\n"
    "📋 /list — общий список чата\n\n"
    "Совет: пишите аргумент сразу вместе с командой (как в примерах выше) — тогда всё "
    "сработает даже если у бота включён privacy mode. Полный список команд — /help"
)

HELP_TEXT = (
    "<b>Основное</b>\n"
    "🔍 /watcher — добавить игру для отслеживания (название / ссылка / ID)\n"
    "⭐ /import_wishlist — импортировать весь вишлист Steam разом\n"
    "📋 /list — список отслеживаемых игр\n"
    "🗑 /delete номер — удалить игру из списка\n"
    "🧹 /clear — очистить весь список разом\n"
    "📰 /digest on|off — дайджест топ-скидок примерно раз в сутки\n"
    "👥 /referral — пригласить друга и получить Premium в подарок\n"
    "✉️ /feedback — сообщить об ошибке или предложить идею\n\n"
    "🔒 /delete_data — удалить персональные данные и список из личного чата\n\n"
    "📄 /privacy — политика конфиденциальности\n"
    "📜 /terms — условия использования и оплаты\n"
    "🆘 /support — поддержка\n\n"
    "🖥 /app — открыть Mini App (если настроен WEBAPP_URL)\n\n"
    "<b>Premium 💎</b>\n"
    "/premium — статус подписки и оформление\n"
    f"🎁 /trial — попробовать Premium бесплатно ({settings.trial_days} дн.), один раз на чат\n"
    f"♾ безлимитный список игр (на бесплатном — до {settings.free_game_limit})\n"
    "/threshold — не уведомлять о скидках меньше X%\n"
    "🏆 алерт «исторический минимум цены»\n"
    "/region — регион/валюта цен (например: /region us)\n"
    "/history номер — история скидок по игре\n"
    "/track_item, /market_list, /market_delete — ценовые алерты по предметам Market\n"
)


@router.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject) -> None:
    payload = (command.args or "").strip()
    if message.chat.type == "private" and payload.startswith(_REFERRAL_PREFIX):
        raw_referrer_id = payload[len(_REFERRAL_PREFIX) :]
        if raw_referrer_id.lstrip("-").isdigit():
            # EnsureRecordsMiddleware уже создал запись этого чата к этому моменту —
            # set_referrer сама проверит, что чат новый (иначе повторный /start ref... не сработает).
            await repository.set_referrer(message.chat.id, int(raw_referrer_id))

    if message.chat.type == "private":
        await message.answer(PRIVATE_WELCOME, reply_markup=main_menu_kb(settings.webapp_url))
    else:
        await message.answer(GROUP_WELCOME)


@router.message(Command("app"))
async def cmd_app(message: types.Message) -> None:
    """Открывает Mini App из Telegram, если задан публичный HTTPS URL."""
    if not settings.webapp_url:
        await message.answer(
            "Mini App ещё не опубликована. Задайте WEBAPP_URL в .env "
            "(публичный HTTPS-адрес) и перезапустите бота."
        )
        return
    await message.answer("Открывайте Mini App кнопкой ниже.", reply_markup=main_menu_kb(settings.webapp_url))


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(HELP_TEXT)
