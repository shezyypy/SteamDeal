from __future__ import annotations

import re
from html import escape

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from app.db import repository
from app.keyboards.inline import premium_offer_kb
from app.services.steam_market import extract_market_item_from_link
from app.states.fsm_states import MarketAlert
from app.utils.links import market_item_link

router = Router(name="market")

_PRICE_RE = re.compile(r"^\d+([.,]\d+)?$")
_MAX_MARKET_NAME_LENGTH = 256  # совпадает с String(256) в TrackedMarketItem.market_hash_name


def _is_price(text: str) -> bool:
    return bool(_PRICE_RE.match(text.strip()))


def _to_cents(price_text: str) -> int:
    return int(round(float(price_text.strip().replace(",", ".")) * 100))


async def _create_alert(message: types.Message, link: str, direction: str, price_text: str) -> None:
    parsed = extract_market_item_from_link(link)
    if parsed is None:
        await message.answer(
            "🔎 Не нашёл ссылку на предмет. Нужна ссылка вида "
            "https://steamcommunity.com/market/listings/730/НазваниеПредмета"
        )
        return

    appid, market_hash_name = parsed
    if len(market_hash_name) > _MAX_MARKET_NAME_LENGTH:
        # Специально сконструированная длинная "ссылка" иначе упала бы ошибкой БД
        # (String(256)) вместо понятного ответа пользователю.
        await message.answer("🔎 Слишком длинное название предмета — проверьте ссылку.")
        return

    threshold_cents = _to_cents(price_text)

    username = message.from_user.username if message.from_user else None
    user_id = message.from_user.id if message.from_user else None

    added = await repository.add_market_alert(
        message.chat.id,
        appid,
        market_hash_name,
        direction,
        threshold_cents,
        added_by_tg_id=user_id,
        added_by_username=username,
    )

    arrow = "упадёт ниже" if direction == "below" else "вырастет выше"
    name = market_item_link(appid, market_hash_name)
    if added:
        await message.answer(
            f"📊 Слежу за «{name}»: сообщу, когда цена {arrow} {threshold_cents / 100:.2f}.\n"
            "Список алертов — /market_list"
        )
    else:
        await message.answer("Такой алерт уже есть — посмотреть все можно через /market_list")


@router.message(Command("track_item"))
async def cmd_track_item(message: types.Message, state: FSMContext, command: CommandObject) -> None:
    if not await repository.is_chat_premium(message.chat.id):
        await message.answer(
            "📊 Отслеживание цен предметов с торговой площадки — Premium-функция.\n"
            "Оформить — /premium",
            reply_markup=premium_offer_kb(),
        )
        return

    args = (command.args or "").strip()
    if args:
        parts = args.split()
        if len(parts) >= 3 and parts[-2].lower() in ("below", "above") and _is_price(parts[-1]):
            link = " ".join(parts[:-2])
            await _create_alert(message, link, parts[-2].lower(), parts[-1])
            return

        await message.answer(
            "Формат: /track_item ссылка_на_предмет below|above цена\n"
            "Например: /track_item https://steamcommunity.com/market/listings/730/Chroma%202%20Case below 5.5"
        )
        return

    await message.answer("📊 Пришлите ссылку на предмет с Steam Community Market.")
    await state.set_state(MarketAlert.waiting_link)


@router.message(MarketAlert.waiting_link)
async def market_waiting_link(message: types.Message, state: FSMContext) -> None:
    link = (message.text or "").strip()
    if extract_market_item_from_link(link) is None:
        await message.answer(
            "🔎 Не нашёл ссылку на предмет. Нужна ссылка вида "
            "https://steamcommunity.com/market/listings/730/НазваниеПредмета"
        )
        return

    await state.update_data(link=link)
    await message.answer(
        "Уведомить, когда цена <b>упадёт ниже</b> или <b>вырастет выше</b> порога? "
        "Напишите одно слово: below или above"
    )
    await state.set_state(MarketAlert.waiting_direction)


@router.message(MarketAlert.waiting_direction)
async def market_waiting_direction(message: types.Message, state: FSMContext) -> None:
    direction = (message.text or "").strip().lower()
    if direction not in ("below", "above"):
        await message.answer("Напишите одно слово: below или above.")
        return

    await state.update_data(direction=direction)
    await message.answer("Укажите пороговую цену числом (в валюте вашего региона), например: 5.5")
    await state.set_state(MarketAlert.waiting_price)


@router.message(MarketAlert.waiting_price)
async def market_waiting_price(message: types.Message, state: FSMContext) -> None:
    price_text = (message.text or "").strip()
    if not _is_price(price_text):
        await message.answer("Нужно число, например 500 или 12.5")
        return

    data = await state.get_data()
    await _create_alert(message, data["link"], data["direction"], price_text)
    await state.clear()


@router.message(Command("market_list"))
async def cmd_market_list(message: types.Message) -> None:
    alerts = await repository.list_market_alerts(message.chat.id)
    if not alerts:
        await message.answer("📊 Список алертов по предметам пуст.\nДобавить — /track_item")
        return

    lines = []
    for i, alert in enumerate(alerts, start=1):
        arrow = "≤" if alert.direction == "below" else "≥"
        item_link = market_item_link(alert.appid, alert.market_hash_name)
        lines.append(f"№{i} {item_link} {arrow} {alert.threshold_cents / 100:.2f}")

    await message.answer(
        "📊 <b>Алерты по предметам:</b>\n" + "\n".join(lines) + "\n\nУдалить — /market_delete номер"
    )


@router.message(Command("market_delete"))
async def cmd_market_delete(message: types.Message, command: CommandObject) -> None:
    alerts = await repository.list_market_alerts(message.chat.id)
    if not alerts:
        await message.answer("📊 Список алертов по предметам пуст.\nДобавить — /track_item")
        return

    args = (command.args or "").strip()
    if not args.isdigit():
        lines = [f"№{i} {market_item_link(a.appid, a.market_hash_name)}" for i, a in enumerate(alerts, start=1)]
        await message.answer("Укажите номер алерта для удаления: /market_delete 1\n\n" + "\n".join(lines))
        return

    index = int(args) - 1
    if not (0 <= index < len(alerts)):
        await message.answer("Такого номера нет — проверьте /market_list.")
        return

    alert = alerts[index]
    await repository.delete_market_alert(alert.id)
    await message.answer(f"🗑 Алерт по «{escape(alert.market_hash_name)}» удалён.")
