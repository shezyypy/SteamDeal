from __future__ import annotations

import logging

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.db import repository
from app.keyboards.inline import WISHLIST_BUTTON
from app.services import steam_catalog
from app.services.crypto import EncryptionNotConfigured
from app.services.referral import reward_referral_on_first_game
from app.services.steam_catalog import SteamCatalogUnavailable
from app.services.steam_wishlist import SteamWishlistUnavailable, fetch_wishlist_appids, resolve_steamid64
from app.states.fsm_states import Wishlist

logger = logging.getLogger(__name__)

router = Router(name="wishlist")


async def _import_wishlist(message: types.Message, profile_ref: str) -> None:
    try:
        async with aiohttp.ClientSession() as session:
            steamid64 = await resolve_steamid64(session, profile_ref)
            if steamid64 is None:
                await message.answer(
                    "🔎 Не нашёл такой профиль Steam.\n"
                    "Пришлите ссылку вида https://steamcommunity.com/id/ВАШ_НИК/, "
                    "https://steamcommunity.com/profiles/<steamid64>/ или сам steamid64."
                )
                return

            appids = await fetch_wishlist_appids(session, steamid64)
            if not appids:
                await message.answer(
                    "📭 Вишлист пуст либо профиль/вишлист приватный.\n"
                    "Сделать вишлист публичным: Steam → Настройки → Приватность → "
                    "Список желаемого → Публичный — и попробуйте снова."
                )
                return

            if message.from_user is not None:
                try:
                    await repository.set_steam_id64(message.from_user.id, steamid64)
                except EncryptionNotConfigured:
                    # Не критично для ТЕКУЩЕГО импорта — теряем только удобство "запомнить
                    # профиль на следующий раз", а не саму функцию импорта вишлиста.
                    logger.warning(
                        "DATA_ENCRYPTION_KEY не задан — steam_id64 не будет запомнен для повторного импорта."
                    )

            games: list[tuple[int, str]] = []
            for appid in appids:
                name = await steam_catalog.resolve_name_by_appid(session, appid)
                games.append((appid, name or f"appid {appid}"))
    except (SteamCatalogUnavailable, SteamWishlistUnavailable) as exc:
        await message.answer(f"⚠️ Не получилось импортировать вишлист: {exc}")
        return

    username = message.from_user.username if message.from_user else None
    user_id = message.from_user.id if message.from_user else None

    existing = await repository.list_tracked_games(message.chat.id)
    existing_appids = {g.appid for g in existing}
    new_games = [(appid, name) for appid, name in games if appid not in existing_appids]
    duplicate_count = len(games) - len(new_games)

    limit_note = ""
    if not await repository.is_chat_premium(message.chat.id):
        remaining = max(settings.free_game_limit - len(existing), 0)
        if len(new_games) > remaining:
            limit_note = (
                f"\n\n⚠️ На бесплатном тарифе лимит {settings.free_game_limit} игр — добавлены "
                f"только первые {remaining} новых из вишлиста. Без ограничений — Premium 💎 /premium"
            )
            new_games = new_games[:remaining]

    added = await repository.add_tracked_games_bulk(
        message.chat.id, new_games, added_by_tg_id=user_id, added_by_username=username
    )

    reply = f"⭐ Готово! Добавлено новых игр: <b>{added}</b>"
    if duplicate_count:
        reply += f" (уже отслеживалось: {duplicate_count})"
    reply += "." + limit_note + "\nПосмотреть список — /list"
    await message.answer(reply)

    if added > 0:
        await reward_referral_on_first_game(message.bot, message.chat.id, len(existing))


@router.message(Command("import_wishlist"))
@router.message(lambda message: message.text == WISHLIST_BUTTON)
async def cmd_import_wishlist(
    message: types.Message, state: FSMContext, command: CommandObject | None = None
) -> None:
    args = (command.args if command else "") or ""
    if args.strip():
        await _import_wishlist(message, args)
        return

    saved_steamid = None
    if message.from_user is not None:
        try:
            saved_steamid = await repository.get_steam_id64(message.from_user.id)
        except EncryptionNotConfigured:
            pass
    if saved_steamid:
        await message.answer("⭐ Импортирую вишлист по сохранённому Steam-профилю…")
        await _import_wishlist(message, saved_steamid)
        return

    await message.answer(
        "⭐ Пришлите ссылку на свой профиль Steam "
        "(например https://steamcommunity.com/id/ВАШ_НИК/) или steamid64 — "
        "импортирую весь вишлист разом, без добавления игр по одной."
    )
    await state.set_state(Wishlist.waiting_profile)


@router.message(Wishlist.waiting_profile)
async def process_wishlist_profile(message: types.Message, state: FSMContext) -> None:
    await _import_wishlist(message, (message.text or "").strip())
    await state.clear()
