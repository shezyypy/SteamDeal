from __future__ import annotations

import re
from html import escape

import aiohttp
from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import repository
from app.keyboards.inline import LIST_BUTTON, WATCHER_BUTTON, clear_confirm_kb, premium_offer_kb
from app.services import steam_catalog
from app.services.referral import reward_referral_on_first_game
from app.services.steam_catalog import SteamCatalogUnavailable
from app.states.fsm_states import Tracking
from app.utils.links import steam_app_link

router = Router(name="tracking")

_NUMERIC_RE = re.compile(r"^\d+$")
_MAX_SUGGESTIONS = 5


async def _add_tracked(
    reply_target: types.Message,
    chat_id: int,
    appid: int,
    name: str,
    user_id: int | None,
    username: str | None,
) -> None:
    # В старой версии это состояние привязки к общему списку `n = []` (main.py) — общая
    # переменная на всех пользователей сразу, из-за чего одновременные запросы двух людей
    # ломали друг другу выбор игры. Здесь список привязан к chat_id (а не к пользователю)
    # специально: в группе список общий на весь чат, в личке chat_id у Telegram и так
    # совпадает с user_id, так что для личных чатов ничего не меняется.
    existing = await repository.list_tracked_games(chat_id)
    if any(g.appid == appid for g in existing):
        await reply_target.answer(f"Игра «{escape(name)}» уже отслеживается здесь 🙂")
        return

    if not await repository.is_chat_premium(chat_id) and len(existing) >= settings.free_game_limit:
        await reply_target.answer(
            f"📈 На бесплатном тарифе можно отслеживать до <b>{settings.free_game_limit}</b> игр "
            f"(сейчас {len(existing)}). Без ограничений — Premium 💎",
            reply_markup=premium_offer_kb(),
        )
        return

    await repository.add_tracked_game(chat_id, appid, name, added_by_tg_id=user_id, added_by_username=username)
    await reply_target.answer(
        f"✅ {steam_app_link(appid, name)} добавлена в список отслеживания!\n"
        "Как только появится скидка — пришлю сюда уведомление 🔔\n\n"
        "Весь список — /list"
    )
    await reward_referral_on_first_game(reply_target.bot, chat_id, len(existing))


async def _resolve_and_track(message: types.Message, raw_text: str) -> None:
    text = raw_text.strip()
    appid: int | None = None

    try:
        async with aiohttp.ClientSession() as session:
            if _NUMERIC_RE.match(text):
                appid = int(text)
            else:
                link_appid = steam_catalog.extract_appid_from_link(text)
                appid = (
                    link_appid
                    if link_appid is not None
                    else await steam_catalog.resolve_appid_by_name(session, text)
                )

            if appid is None:
                # Точного совпадения нет — предлагаем похожие варианты вместо того, чтобы
                # заставлять подбирать регистр/пунктуацию вручную (см. tests/test_steam_catalog_fuzzy.py).
                suggestions = await steam_catalog.suggest_matches(session, text, limit=_MAX_SUGGESTIONS)
                if not suggestions:
                    await message.answer(
                        "🔎 Не нашёл такую игру в Steam.\n"
                        "Проверьте название или пришлите ссылку на страницу игры / её ID "
                        '(<a href="https://steamdb.info">SteamDB</a>).'
                    )
                    return

                builder = InlineKeyboardBuilder()
                for suggestion_appid, suggestion_name in suggestions:
                    builder.button(text=suggestion_name[:60], callback_data=f"pick_game:{suggestion_appid}")
                builder.adjust(1)
                await message.answer(
                    "🔎 Точного совпадения нет. Может, вы имели в виду:", reply_markup=builder.as_markup()
                )
                return

            # Резолвим настоящее название по appid — иначе если ввели ссылку или голый ID,
            # в /list и в уведомлениях был бы виден не тайтл игры, а сама ссылка/цифры.
            name = await steam_catalog.resolve_name_by_appid(session, appid) or text
    except SteamCatalogUnavailable as exc:
        await message.answer(f"⚠️ Поиск игр сейчас недоступен: {exc}")
        return

    username = message.from_user.username if message.from_user else None
    user_id = message.from_user.id if message.from_user else None
    await _add_tracked(message, message.chat.id, appid, name, user_id, username)


@router.callback_query(F.data.startswith("pick_game:"))
async def pick_suggested_game(callback: types.CallbackQuery) -> None:
    appid = int(callback.data.split(":", 1)[1])

    async with aiohttp.ClientSession() as session:
        try:
            name = await steam_catalog.resolve_name_by_appid(session, appid)
        except SteamCatalogUnavailable:
            name = None

    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
        await _add_tracked(
            callback.message,
            callback.message.chat.id,
            appid,
            name or f"appid {appid}",
            callback.from_user.id,
            callback.from_user.username,
        )
    await callback.answer()


@router.message(Command("watcher"))
@router.message(F.text == WATCHER_BUTTON)
async def cmd_watcher(message: types.Message, state: FSMContext, command: CommandObject | None = None) -> None:
    args = (command.args if command else "") or ""
    if args.strip():
        await _resolve_and_track(message, args)
        return

    await message.answer(
        "🎮 Пришлите название игры (точно как в Steam), ссылку на её страницу в Steam "
        'или её ID (можно найти на <a href="https://steamdb.info">SteamDB</a>).'
    )
    await state.set_state(Tracking.waiting_game_input)


@router.message(Tracking.waiting_game_input)
async def process_game_input(message: types.Message, state: FSMContext) -> None:
    await _resolve_and_track(message, message.text or "")
    await state.clear()


@router.message(Command("list"))
@router.message(F.text == LIST_BUTTON)
async def cmd_list(message: types.Message) -> None:
    games = await repository.list_tracked_games(message.chat.id)
    if not games:
        await message.answer(
            "📭 Список пока пуст.\nДобавьте игру — /watcher, или импортируйте вишлист целиком — /import_wishlist"
        )
        return

    is_group = message.chat.type != "private"
    lines = []
    for i, game in enumerate(games, start=1):
        line = f"№{i} {steam_app_link(game.appid, game.name)}"
        if is_group and game.added_by_username:
            line += f" <i>(добавил(а) @{escape(game.added_by_username)})</i>"
        lines.append(line)

    await message.answer(
        "🎮 <b>Список отслеживаемых игр:</b>\n" + "\n".join(lines) + "\n\nУдалить — /delete номер"
    )


@router.message(Command("delete"))
async def cmd_delete(message: types.Message, command: CommandObject) -> None:
    games = await repository.list_tracked_games(message.chat.id)
    if not games:
        await message.answer(
            "📭 Список пока пуст.\nДобавьте игру — /watcher, или импортируйте вишлист целиком — /import_wishlist"
        )
        return

    args = (command.args or "").strip()
    if not args.isdigit():
        lines = [f"№{i} {steam_app_link(game.appid, game.name)}" for i, game in enumerate(games, start=1)]
        await message.answer("Укажите номер игры для удаления, например: /delete 1\n\n" + "\n".join(lines))
        return

    index = int(args) - 1
    if not (0 <= index < len(games)):
        await message.answer("Такого номера нет в списке — проверьте /list.")
        return

    game = games[index]
    await repository.delete_tracked_game(message.chat.id, game.appid)
    await message.answer(f"🗑 «{escape(game.name)}» удалена из списка отслеживания.\nВесь список — /list")


@router.message(Command("clear"))
async def cmd_clear(message: types.Message) -> None:
    games = await repository.list_tracked_games(message.chat.id)
    if not games:
        await message.answer("📭 Список и так пуст.")
        return

    await message.answer(
        f"Точно очистить весь список ({len(games)} игр)? Это действие нельзя отменить.",
        reply_markup=clear_confirm_kb(),
    )


@router.callback_query(F.data == "clear:confirm")
async def clear_confirm(callback: types.CallbackQuery) -> None:
    count = await repository.delete_all_tracked_games(callback.message.chat.id)
    await callback.message.edit_text(f"🗑 Список очищен, удалено игр: {count}.")
    await callback.answer()


@router.callback_query(F.data == "clear:cancel")
async def clear_cancel(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text("Отменено — список не тронут.")
    await callback.answer()
