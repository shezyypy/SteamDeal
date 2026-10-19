from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import LabeledPrice, PreCheckoutQuery

from app.config import settings
from app.db import repository
from app.keyboards.inline import premium_offer_kb
from app.utils.links import steam_app_link

logger = logging.getLogger(__name__)

router = Router(name="premium")

_PAYLOAD_PREFIX = "premium:"

_VALID_REGIONS = {
    "ru": "Россия (₽)",
    "us": "США ($)",
    "ua": "Украина (₴)",
    "kz": "Казахстан (₸)",
    "tr": "Турция (₺)",
    "de": "Германия (€)",
    "gb": "Великобритания (£)",
}

_PREMIUM_PITCH = (
    "<b>Premium 💎</b>\n"
    "♾ Безлимитный список игр — на бесплатном тарифе лимит "
    f"{settings.free_game_limit}\n"
    f"🚀 Ускоренная проверка скидок — примерно раз в {settings.premium_poll_interval_minutes} мин. "
    f"вместо {settings.sale_poll_interval_minutes}\n"
    "🎚 Свой порог скидки — не уведомлять о мелких скидках, /threshold\n"
    "🏆 Алерт «исторический минимум цены» — если игра ещё никогда не была дешевле\n"
    "🌍 Свой регион/валюта цен — /region\n"
    "📈 История скидок по играм — /history\n"
    "📊 Ценовые алерты по предметам Market (скины, кейсы) — /track_item\n\n"
    f"Стоимость: <b>{settings.premium_price_stars} ⭐ Stars</b> / {settings.premium_days} дней"
)


def _format_until(until: dt.datetime) -> str:
    return until.strftime("%d.%m.%Y")


def _is_premium(chat) -> bool:
    return bool(chat and chat.premium_until and chat.premium_until > dt.datetime.now(dt.timezone.utc))


def _make_payload(chat_id: int) -> str:
    return f"{_PAYLOAD_PREFIX}{chat_id}"


def _parse_payload(payload: str) -> int | None:
    """Проверка формата payload — сам payload формируем мы же в _send_invoice, но так
    подтверждение платежа не завязано на слепое доверие полю, пришедшему от Telegram, и
    не сломается молча, если в будущем появится второй тип платежа с другим payload."""
    if not payload.startswith(_PAYLOAD_PREFIX):
        return None
    try:
        return int(payload[len(_PAYLOAD_PREFIX) :])
    except ValueError:
        return None


def _is_expected_payment(currency: str, total_amount: int) -> bool:
    return currency == "XTR" and int(total_amount) == settings.premium_price_stars


async def _send_invoice(message: types.Message) -> None:
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title="SteamSaleBot Premium",
        description=(
            f"{settings.premium_days} дней Premium-доступа: ускоренные уведомления, "
            "свой регион цен, история скидок."
        ),
        payload=_make_payload(message.chat.id),
        currency="XTR",
        prices=[
            LabeledPrice(label=f"Premium на {settings.premium_days} дней", amount=settings.premium_price_stars)
        ],
    )


@router.message(Command("premium"))
async def cmd_premium(message: types.Message) -> None:
    chat = await repository.get_chat(message.chat.id)
    now = dt.datetime.now(dt.timezone.utc)

    if chat and chat.premium_until and chat.premium_until > now:
        await message.answer(
            f"💎 Premium активен до <b>{_format_until(chat.premium_until)}</b>.\n"
            "Регион цен — /region, история скидок — /history"
        )
        return

    await message.answer(_PREMIUM_PITCH, reply_markup=premium_offer_kb())


@router.message(Command("trial"))
async def cmd_trial(message: types.Message) -> None:
    chat = await repository.get_chat(message.chat.id)

    if _is_premium(chat):
        await message.answer(
            f"💎 У вас уже есть Premium до <b>{_format_until(chat.premium_until)}</b> — "
            "пробный период тут ни к чему 🙂"
        )
        return

    if chat and chat.trial_used_at is not None:
        await message.answer(
            "Пробный период уже был использован этим чатом. Оформить постоянный Premium — /premium",
            reply_markup=premium_offer_kb(),
        )
        return

    granted = await repository.grant_trial(message.chat.id, settings.trial_days)
    if not granted:
        await message.answer("Не получилось выдать пробный период — попробуйте /premium.")
        return

    await message.answer(
        f"🎁 Пробный Premium на <b>{settings.trial_days} дн.</b> активирован — попробуйте "
        "/threshold, /region, /history, /track_item.\nПонравится — оформить насовсем: /premium"
    )


@router.callback_query(F.data == "premium:buy")
async def buy_premium(callback: types.CallbackQuery) -> None:
    await _send_invoice(callback.message)
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    # Telegram даёт 10 секунд на ответ — тут не обращаемся к БД/сети, только проверяем
    # формат payload (см. _parse_payload).
    if (
        _parse_payload(pre_checkout_query.invoice_payload) is None
        or not _is_expected_payment(pre_checkout_query.currency, pre_checkout_query.total_amount)
    ):
        await pre_checkout_query.answer(
            ok=False, error_message="Что-то пошло не так с заказом — оформите Premium заново через /premium."
        )
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message) -> None:
    payload_chat_id = _parse_payload(message.successful_payment.invoice_payload)
    if payload_chat_id is None or payload_chat_id != message.chat.id:
        logger.warning(
            "Успешный платёж с несовпадающим payload=%r для chat_id=%s — Premium не выдан",
            message.successful_payment.invoice_payload,
            message.chat.id,
        )
        await message.answer(
            "⚠️ Платёж прошёл, но не подтвердился автоматически — напишите /feedback, разберёмся вручную."
        )
        return

    payment = message.successful_payment
    if not _is_expected_payment(payment.currency, payment.total_amount):
        logger.warning(
            "Успешный платёж с неожиданной суммой/валютой currency=%s amount=%s chat_id=%s — Premium не выдан",
            payment.currency,
            payment.total_amount,
            message.chat.id,
        )
        await message.answer(
            "⚠️ Платёж прошёл, но сумма не совпала с текущим тарифом — напишите /feedback, разберёмся вручную."
        )
        return
    granted = await repository.record_payment_and_grant_premium(
        message.chat.id,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        provider_payment_charge_id=payment.provider_payment_charge_id,
        invoice_payload=payment.invoice_payload,
        currency=payment.currency,
        total_amount=payment.total_amount,
        premium_days=settings.premium_days,
    )
    if not granted:
        await message.answer("✅ Этот платёж уже обработан — Premium повторно не списан.")
        return
    chat = await repository.get_chat(message.chat.id)
    until = chat.premium_until if chat and chat.premium_until else dt.datetime.now(dt.timezone.utc)
    await message.answer(
        f"🎉 Premium активирован до <b>{_format_until(until)}</b>!\n"
        "Настроить регион цен — /region, история скидок — /history"
    )


@router.message(Command("region"))
async def cmd_region(message: types.Message, command: CommandObject) -> None:
    chat = await repository.get_chat(message.chat.id)
    is_premium = _is_premium(chat)

    args = (command.args or "").strip().lower()
    options = "\n".join(f"• {code} — {label}" for code, label in _VALID_REGIONS.items())

    if not args:
        current = chat.price_region if chat else "ru"
        await message.answer(
            f"🌍 Текущий регион цен: <b>{current}</b>\n\n"
            f"Сменить: /region код (например /region us). Доступные коды:\n{options}"
        )
        return

    if not is_premium:
        await message.answer(
            "🌍 Смена региона цен — Premium-функция.\nОформить — /premium", reply_markup=premium_offer_kb()
        )
        return

    if args not in _VALID_REGIONS:
        await message.answer(f"Неизвестный код региона. Доступные:\n{options}")
        return

    await repository.set_price_region(message.chat.id, args)
    await message.answer(f"✅ Регион цен обновлён: <b>{args}</b> — {_VALID_REGIONS[args]}")


@router.message(Command("threshold"))
async def cmd_threshold(message: types.Message, command: CommandObject) -> None:
    chat = await repository.get_chat(message.chat.id)
    is_premium = _is_premium(chat)

    args = (command.args or "").strip()

    if not args:
        current = chat.min_discount_percent if chat else 0
        await message.answer(
            f"🎚 Сейчас уведомляю о скидках от <b>{current}%</b>.\n"
            "Изменить: /threshold число (например /threshold 30, чтобы не получать уведомления "
            "о скидках меньше 30%)."
        )
        return

    if not is_premium:
        await message.answer(
            "🎚 Настройка порога скидки — Premium-функция.\nОформить — /premium",
            reply_markup=premium_offer_kb(),
        )
        return

    if not args.isdigit() or not (0 <= int(args) <= 95):
        await message.answer("Укажите число от 0 до 95, например /threshold 30.")
        return

    await repository.set_min_discount(message.chat.id, int(args))
    await message.answer(f"✅ Теперь уведомляю только о скидках от <b>{args}%</b>.")


@router.message(Command("history"))
async def cmd_history(message: types.Message, command: CommandObject) -> None:
    chat = await repository.get_chat(message.chat.id)
    is_premium = _is_premium(chat)
    if not is_premium:
        await message.answer(
            "📈 История скидок — Premium-функция.\nОформить — /premium", reply_markup=premium_offer_kb()
        )
        return

    games = await repository.list_tracked_games(message.chat.id)
    if not games:
        await message.answer("Список отслеживания пуст — сначала добавьте игру через /watcher.")
        return

    args = (command.args or "").strip()
    target = None
    if args.isdigit():
        index = int(args) - 1
        if 0 <= index < len(games):
            target = games[index]

    if target is None:
        lines = [f"№{i} {steam_app_link(g.appid, g.name)}" for i, g in enumerate(games, start=1)]
        await message.answer("Укажите номер игры: /history номер\n\n" + "\n".join(lines))
        return

    snapshots = await repository.price_history(target.appid, limit=10)
    if not snapshots:
        await message.answer(
            f"По игре {steam_app_link(target.appid, target.name)} пока нет истории скидок — "
            "она появится после первой найденной распродажи."
        )
        return

    lines = [
        f'{s.fetched_at.strftime("%d.%m.%Y")}: -{s.discount_percent}% → {s.price_final_cents / 100:.0f} {s.currency}'
        for s in snapshots
    ]
    await message.answer(
        f"📈 <b>История скидок «{steam_app_link(target.appid, target.name)}»:</b>\n" + "\n".join(lines)
    )
