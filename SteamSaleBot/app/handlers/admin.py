from __future__ import annotations

import datetime as dt

from aiogram import Router, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import BaseFilter, Command, CommandObject

from app.config import settings
from app.db import repository
from app.services.rate_limit import RateLimiter

router = Router(name="admin")

# Telegram позволяет рассылать в разные чаты примерно 30 сообщений/сек — берём с запасом,
# чтобы не словить flood control при большой базе чатов.
_broadcast_limiter = RateLimiter(max_calls=25, period_seconds=1)


class IsAdmin(BaseFilter):
    """Единственная проверка прав во всей админке — просто chat_id == ADMIN_CHAT_ID.
    Никакого отдельного логина не нужно: сама авторизация в Telegram уже надёжна."""

    async def __call__(self, message: types.Message) -> bool:
        return settings.admin_chat_id is not None and message.chat.id == settings.admin_chat_id


def _format_heartbeat(job_name: str, label: str, heartbeats: dict[str, dt.datetime], interval_minutes: int) -> str:
    last_run = heartbeats.get(job_name)
    if last_run is None:
        return f"{label}: ещё ни разу не запускался ❔"

    age_minutes = (dt.datetime.now(dt.timezone.utc) - last_run).total_seconds() / 60
    # Даём запас в 3 интервала цикла, прежде чем считать воркер подозрительно тихим —
    # одиночный медленный цикл (например, из-за rate-limit паузы) не должен пугать зря.
    is_stale = age_minutes > interval_minutes * 3
    status = "⚠️ похоже, воркер не работает" if is_stale else "✅"
    return f"{label}: последний цикл {age_minutes:.0f} мин назад {status}"


@router.message(Command("admin_stats"), IsAdmin())
async def cmd_admin_stats(message: types.Message) -> None:
    stats = await repository.admin_stats()
    heartbeats = stats["heartbeats"]

    lines = [
        "📊 <b>Статистика бота</b>",
        f"Чатов всего: {stats['total_chats']} (личных: {stats['private_chats']}, групп: {stats['group_chats']})",
        f"Premium активных: {stats['premium_chats']}",
        f"Заблокировали бота: {stats['blocked_chats']}",
        f"Игр в отслеживании: {stats['total_tracked_games']}",
        f"Market-алертов: {stats['total_market_alerts']}",
        f"Платежей: {stats.get('payments_count', 0)} · выручка: {stats.get('stars_revenue', 0)} ⭐",
        "",
        "<b>Воркер (heartbeat)</b>",
        _format_heartbeat("check_sales_free", "Обычные чаты", heartbeats, settings.sale_poll_interval_minutes),
        _format_heartbeat(
            "check_sales_premium", "Premium-чаты", heartbeats, settings.premium_poll_interval_minutes
        ),
        _format_heartbeat(
            "check_market_alerts", "Market-алерты", heartbeats, settings.market_poll_interval_minutes
        ),
    ]
    await message.answer("\n".join(lines))


@router.message(Command("admin_premium"), IsAdmin())
async def cmd_admin_premium(message: types.Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if not args or not args[0].lstrip("-").isdigit():
        await message.answer("Формат: /admin_premium chat_id [дней=30]")
        return

    chat_id = int(args[0])
    days = int(args[1]) if len(args) > 1 and args[1].isdigit() else settings.premium_days

    chat = await repository.get_chat(chat_id)
    if chat is None:
        await message.answer(f"Чат {chat_id} боту не известен (не было ни одного апдейта от него).")
        return

    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    await repository.grant_premium(chat_id, until)
    await message.answer(f"✅ Premium для {chat_id} выдан до {until.strftime('%d.%m.%Y')}.")


@router.message(Command("admin_revoke"), IsAdmin())
async def cmd_admin_revoke(message: types.Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args.lstrip("-").isdigit():
        await message.answer("Формат: /admin_revoke chat_id")
        return

    chat_id = int(args)
    chat = await repository.get_chat(chat_id)
    if chat is None:
        await message.answer(f"Чат {chat_id} боту не известен.")
        return

    await repository.grant_premium(chat_id, dt.datetime.now(dt.timezone.utc))
    await message.answer(f"Premium для {chat_id} отозван.")


@router.message(Command("admin_blocked"), IsAdmin())
async def cmd_admin_blocked(message: types.Message) -> None:
    blocked = await repository.list_blocked_chats(limit=30)
    if not blocked:
        await message.answer("Заблокировавших бота чатов нет 🎉")
        return

    lines = [
        f"{chat.chat_id} ({chat.chat_type}) — с {chat.blocked_at.strftime('%d.%m.%Y')}" for chat in blocked
    ]
    await message.answer("🚫 <b>Заблокировали бота:</b>\n" + "\n".join(lines))


@router.message(Command("admin_broadcast"), IsAdmin())
async def cmd_admin_broadcast(message: types.Message, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer("Формат: /admin_broadcast текст сообщения")
        return

    chat_ids = await repository.all_chat_ids(exclude_blocked=True)
    await message.answer(f"📣 Рассылаю {len(chat_ids)} чатам…")

    sent = 0
    failed = 0
    for chat_id in chat_ids:
        await _broadcast_limiter.acquire()
        try:
            await message.bot.send_message(chat_id, text)
            sent += 1
        except TelegramForbiddenError:
            await repository.mark_chat_blocked(chat_id)
            failed += 1
        except Exception:
            failed += 1

    await message.answer(f"✅ Разослано: {sent}, не доставлено: {failed}")


@router.message(Command("admin_help"))
async def cmd_admin_help(message: types.Message) -> None:
    # Не гейтим этот конкретный хендлер IsAdmin — если случайный пользователь наберёт
    # /admin_help, лучше молча промолчать, чем показать список админ-команд. Поэтому
    # проверяем прямо внутри и просто ничего не отвечаем не-админу.
    if settings.admin_chat_id is None or message.chat.id != settings.admin_chat_id:
        return

    await message.answer(
        "<b>Админ-команды</b>\n"
        "/admin_stats — статистика бота и воркера\n"
        "/admin_premium chat_id [дней] — выдать Premium\n"
        "/admin_revoke chat_id — отозвать Premium\n"
        "/admin_blocked — список чатов, заблокировавших бота\n"
        "/admin_broadcast текст — разослать сообщение всем чатам"
    )
