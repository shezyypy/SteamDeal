from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select

from app.db.base import session_scope
from app.db.models import Chat, NotificationLog, SaleSnapshot, TrackedGame, TrackedMarketItem, User, WorkerHeartbeat
from app.services.crypto import decrypt_str, encrypt_str

# Всё через ORM с bind-параметрами — f-строк внутри SQL нет нигде, инъекция через
# название игры/ник/steamid невозможна в принципе.

_MAX_NAME_LENGTH = 256  # совпадает с String(256) в моделях — обрезаем здесь, а не полагаемся
# на то, что все места вызова сами проверят длину перед вставкой.


# --- чаты и пользователи -----------------------------------------------------------------


async def get_or_create_chat(chat_id: int, chat_type: str, title: str | None) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            session.add(Chat(chat_id=chat_id, chat_type=chat_type, title=title))
            await session.commit()
            return

        changed = False
        if chat.chat_type != chat_type or chat.title != title:
            chat.chat_type = chat_type
            chat.title = title
            changed = True
        if chat.blocked_at is not None:
            # Чат снова написал боту — значит, он точно не заблокирован (иначе апдейт
            # просто не пришёл бы). Снимаем пометку, чтобы опрос скидок возобновился.
            chat.blocked_at = None
            changed = True
        if changed:
            await session.commit()


async def get_or_create_user(tg_id: int, username: str | None) -> None:
    async with session_scope() as session:
        user = await session.get(User, tg_id)
        if user is None:
            session.add(User(tg_id=tg_id, username=username))
            await session.commit()
        elif user.username != username:
            user.username = username
            await session.commit()


async def get_chat(chat_id: int) -> Chat | None:
    async with session_scope() as session:
        return await session.get(Chat, chat_id)


async def set_steam_id64(tg_id: int, steam_id64: str) -> None:
    """steam_id64 хранится в БД зашифрованным (app/services/crypto.py) — для вызывающего
    кода это прозрачно, шифрование/дешифрование происходит только здесь."""
    encrypted = encrypt_str(steam_id64)
    async with session_scope() as session:
        user = await session.get(User, tg_id)
        if user is not None:
            user.steam_id64 = encrypted
            await session.commit()


async def get_steam_id64(tg_id: int) -> str | None:
    async with session_scope() as session:
        user = await session.get(User, tg_id)
        if user is None or user.steam_id64 is None:
            return None
        return decrypt_str(user.steam_id64)


# --- отслеживаемые игры --------------------------------------------------------------------


async def add_tracked_game(
    chat_id: int,
    appid: int,
    name: str,
    added_by_tg_id: int | None = None,
    added_by_username: str | None = None,
) -> bool:
    """Возвращает False, если чат уже отслеживает эту игру."""
    async with session_scope() as session:
        existing = await session.scalar(
            select(TrackedGame).where(TrackedGame.chat_id == chat_id, TrackedGame.appid == appid)
        )
        if existing:
            return False
        session.add(
            TrackedGame(
                chat_id=chat_id,
                appid=appid,
                name=name[:_MAX_NAME_LENGTH],
                added_by_tg_id=added_by_tg_id,
                added_by_username=added_by_username,
            )
        )
        await session.commit()
        return True


async def add_tracked_games_bulk(
    chat_id: int,
    games: list[tuple[int, str]],
    added_by_tg_id: int | None = None,
    added_by_username: str | None = None,
) -> int:
    """Для импорта вишлиста разом. Возвращает число реально добавленных (новых) игр."""
    added = 0
    async with session_scope() as session:
        existing_appids = set(
            await session.scalars(select(TrackedGame.appid).where(TrackedGame.chat_id == chat_id))
        )
        for appid, name in games:
            if appid in existing_appids:
                continue
            session.add(
                TrackedGame(
                    chat_id=chat_id,
                    appid=appid,
                    name=name[:_MAX_NAME_LENGTH],
                    added_by_tg_id=added_by_tg_id,
                    added_by_username=added_by_username,
                )
            )
            existing_appids.add(appid)
            added += 1
        await session.commit()
    return added


async def list_tracked_games(chat_id: int) -> list[TrackedGame]:
    async with session_scope() as session:
        result = await session.scalars(
            select(TrackedGame).where(TrackedGame.chat_id == chat_id).order_by(TrackedGame.added_at)
        )
        return list(result)


async def delete_tracked_game(chat_id: int, appid: int) -> bool:
    async with session_scope() as session:
        result = await session.execute(
            delete(TrackedGame).where(TrackedGame.chat_id == chat_id, TrackedGame.appid == appid)
        )
        await session.commit()
        return result.rowcount > 0


async def delete_all_tracked_games(chat_id: int) -> int:
    async with session_scope() as session:
        result = await session.execute(delete(TrackedGame).where(TrackedGame.chat_id == chat_id))
        await session.commit()
        return result.rowcount


async def appids_by_region(premium_only: bool) -> dict[str, list[int]]:
    """Группирует appid отслеживаемых игр по региону цен их чата — отдельно для
    премиум-чатов (быстрый цикл опроса) и обычных (медленный). См. app/scheduler/jobs.py."""
    now = dt.datetime.now(dt.timezone.utc)
    async with session_scope() as session:
        if premium_only:
            premium_condition = (Chat.premium_until.is_not(None)) & (Chat.premium_until > now)
        else:
            premium_condition = (Chat.premium_until.is_(None)) | (Chat.premium_until <= now)

        rows = (
            await session.execute(
                select(Chat.price_region, TrackedGame.appid)
                .join(TrackedGame, TrackedGame.chat_id == Chat.chat_id)
                .where(premium_condition, Chat.blocked_at.is_(None))
                .distinct()
            )
        ).all()

    grouped: dict[str, list[int]] = {}
    for region, appid in rows:
        grouped.setdefault(region, []).append(appid)
    return grouped


async def chats_tracking_appid_in_region(appid: int, region: str) -> list[tuple[int, int]]:
    """Возвращает пары (chat_id, min_discount_percent) — фильтр по порогу скидки
    применяется в notifier.py на каждый чат отдельно, порог у всех может отличаться."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(TrackedGame.chat_id, Chat.min_discount_percent)
                .join(Chat, Chat.chat_id == TrackedGame.chat_id)
                .where(
                    TrackedGame.appid == appid,
                    Chat.price_region == region,
                    Chat.blocked_at.is_(None),
                )
            )
        ).all()
        return [(row[0], row[1]) for row in rows]


# --- уведомления -----------------------------------------------------------------------------


async def already_notified(chat_id: int, appid: int, discount_percent: int) -> bool:
    async with session_scope() as session:
        existing = await session.scalar(
            select(NotificationLog).where(
                NotificationLog.chat_id == chat_id,
                NotificationLog.appid == appid,
                NotificationLog.discount_percent == discount_percent,
            )
        )
        return existing is not None


async def log_notification(chat_id: int, appid: int, discount_percent: int) -> None:
    async with session_scope() as session:
        session.add(NotificationLog(chat_id=chat_id, appid=appid, discount_percent=discount_percent))
        await session.commit()


# --- premium -----------------------------------------------------------------------------------


async def grant_premium(chat_id: int, until: dt.datetime) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            chat.premium_until = until
            await session.commit()


async def is_chat_premium(chat_id: int) -> bool:
    chat = await get_chat(chat_id)
    if chat is None or chat.premium_until is None:
        return False
    return chat.premium_until > dt.datetime.now(dt.timezone.utc)


async def set_price_region(chat_id: int, region: str) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            chat.price_region = region
            await session.commit()


async def set_min_discount(chat_id: int, percent: int) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            chat.min_discount_percent = percent
            await session.commit()


# --- история цен (Premium) ----------------------------------------------------------------------


async def price_history(appid: int, limit: int = 10) -> list[SaleSnapshot]:
    async with session_scope() as session:
        result = await session.scalars(
            select(SaleSnapshot)
            .where(SaleSnapshot.appid == appid)
            .order_by(SaleSnapshot.fetched_at.desc())
            .limit(limit)
        )
        return list(result)


async def is_all_time_low(appid: int, price_final_cents: int) -> bool:
    """True, только если раньше уже были записи по этой игре и новая цена ниже всех них —
    вызывать ДО записи текущего снэпшота, иначе он посчитает сам себя минимумом."""
    async with session_scope() as session:
        current_min = await session.scalar(
            select(func.min(SaleSnapshot.price_final_cents)).where(SaleSnapshot.appid == appid)
        )
    return current_min is not None and price_final_cents < current_min


# --- алерты по предметам с Market (Premium) -----------------------------------------------------


async def add_market_alert(
    chat_id: int,
    appid: int,
    market_hash_name: str,
    direction: str,
    threshold_cents: int,
    added_by_tg_id: int | None = None,
    added_by_username: str | None = None,
) -> bool:
    """Возвращает False, если у чата уже есть такой алерт (тот же предмет+направление)."""
    async with session_scope() as session:
        existing = await session.scalar(
            select(TrackedMarketItem).where(
                TrackedMarketItem.chat_id == chat_id,
                TrackedMarketItem.market_hash_name == market_hash_name,
                TrackedMarketItem.direction == direction,
            )
        )
        if existing:
            return False
        session.add(
            TrackedMarketItem(
                chat_id=chat_id,
                appid=appid,
                market_hash_name=market_hash_name,
                direction=direction,
                threshold_cents=threshold_cents,
                added_by_tg_id=added_by_tg_id,
                added_by_username=added_by_username,
            )
        )
        await session.commit()
        return True


async def list_market_alerts(chat_id: int) -> list[TrackedMarketItem]:
    async with session_scope() as session:
        result = await session.scalars(
            select(TrackedMarketItem)
            .where(TrackedMarketItem.chat_id == chat_id)
            .order_by(TrackedMarketItem.added_at)
        )
        return list(result)


async def delete_market_alert(alert_id: int) -> None:
    async with session_scope() as session:
        await session.execute(delete(TrackedMarketItem).where(TrackedMarketItem.id == alert_id))
        await session.commit()


async def all_market_alerts() -> list[dict]:
    """Плоский список словарей (а не ORM-объектов) с уже подтянутым регионом чата —
    используется планировщиком, которому не нужна отдельная сессия/lazy-load."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    TrackedMarketItem.id,
                    TrackedMarketItem.chat_id,
                    TrackedMarketItem.appid,
                    TrackedMarketItem.market_hash_name,
                    TrackedMarketItem.direction,
                    TrackedMarketItem.threshold_cents,
                    Chat.price_region,
                )
                .join(Chat, Chat.chat_id == TrackedMarketItem.chat_id)
                .where(Chat.blocked_at.is_(None))
            )
        ).all()

    return [
        {
            "id": row.id,
            "chat_id": row.chat_id,
            "appid": row.appid,
            "market_hash_name": row.market_hash_name,
            "direction": row.direction,
            "threshold_cents": row.threshold_cents,
            "region": row.price_region,
        }
        for row in rows
    ]


# --- заблокировавшие бота чаты -------------------------------------------------------------------


async def mark_chat_blocked(chat_id: int) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            chat.blocked_at = dt.datetime.now(dt.timezone.utc)
            await session.commit()


async def list_blocked_chats(limit: int = 30) -> list[Chat]:
    async with session_scope() as session:
        result = await session.scalars(
            select(Chat).where(Chat.blocked_at.is_not(None)).order_by(Chat.blocked_at.desc()).limit(limit)
        )
        return list(result)


async def all_chat_ids(exclude_blocked: bool = True) -> list[int]:
    async with session_scope() as session:
        query = select(Chat.chat_id)
        if exclude_blocked:
            query = query.where(Chat.blocked_at.is_(None))
        result = await session.scalars(query)
        return list(result)


# --- heartbeat планировщика (для /admin_stats) ---------------------------------------------------


async def record_heartbeat(job_name: str) -> None:
    async with session_scope() as session:
        hb = await session.get(WorkerHeartbeat, job_name)
        now = dt.datetime.now(dt.timezone.utc)
        if hb is None:
            session.add(WorkerHeartbeat(job_name=job_name, last_run_at=now))
        else:
            hb.last_run_at = now
        await session.commit()


async def get_heartbeats() -> dict[str, dt.datetime]:
    async with session_scope() as session:
        result = await session.scalars(select(WorkerHeartbeat))
        return {hb.job_name: hb.last_run_at for hb in result}


# --- статистика для /admin_stats -----------------------------------------------------------------


async def admin_stats() -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    async with session_scope() as session:
        total_chats = await session.scalar(select(func.count()).select_from(Chat))
        private_chats = await session.scalar(
            select(func.count()).select_from(Chat).where(Chat.chat_type == "private")
        )
        premium_chats = await session.scalar(
            select(func.count())
            .select_from(Chat)
            .where(Chat.premium_until.is_not(None), Chat.premium_until > now)
        )
        blocked_chats = await session.scalar(
            select(func.count()).select_from(Chat).where(Chat.blocked_at.is_not(None))
        )
        total_tracked_games = await session.scalar(select(func.count()).select_from(TrackedGame))
        total_market_alerts = await session.scalar(select(func.count()).select_from(TrackedMarketItem))

    return {
        "total_chats": total_chats or 0,
        "private_chats": private_chats or 0,
        "group_chats": (total_chats or 0) - (private_chats or 0),
        "premium_chats": premium_chats or 0,
        "blocked_chats": blocked_chats or 0,
        "total_tracked_games": total_tracked_games or 0,
        "total_market_alerts": total_market_alerts or 0,
        "heartbeats": await get_heartbeats(),
    }


# --- пробный Premium (/trial) --------------------------------------------------------------------


async def grant_trial(chat_id: int, days: int) -> bool:
    """Возвращает False, если пробный период уже был использован этим чатом."""
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None or chat.trial_used_at is not None:
            return False

        now = dt.datetime.now(dt.timezone.utc)
        chat.trial_used_at = now
        chat.premium_until = now + dt.timedelta(days=days)
        await session.commit()
        return True


# --- реферальная программа ------------------------------------------------------------------------


async def set_referrer(chat_id: int, referrer_chat_id: int) -> bool:
    """True, только если чат новый (ещё без referred_by) и не сам себе реферер."""
    if chat_id == referrer_chat_id:
        return False

    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        referrer = await session.get(Chat, referrer_chat_id)
        if chat is None or referrer is None or chat.referred_by is not None:
            return False

        chat.referred_by = referrer_chat_id
        await session.commit()
        return True


async def try_reward_referral(chat_id: int, reward_days: int) -> int | None:
    """Если у chat_id есть непрошенный referrer и награда ещё не выдавалась — продлевает
    Premium рефереру на reward_days. Возвращает chat_id реферера (чтобы его уведомить)
    или None, если награждать было некого/нечего."""
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None or chat.referred_by is None or chat.referral_rewarded:
            return None

        referrer = await session.get(Chat, chat.referred_by)
        if referrer is None:
            return None

        now = dt.datetime.now(dt.timezone.utc)
        base = referrer.premium_until if referrer.premium_until and referrer.premium_until > now else now
        referrer.premium_until = base + dt.timedelta(days=reward_days)
        chat.referral_rewarded = True
        await session.commit()
        return referrer.chat_id


# --- дайджест топ-скидок (/digest) -----------------------------------------------------------------


async def set_digest_enabled(chat_id: int, enabled: bool) -> None:
    async with session_scope() as session:
        chat = await session.get(Chat, chat_id)
        if chat is not None:
            chat.digest_enabled = enabled
            await session.commit()


async def chats_with_digest_enabled() -> list[int]:
    async with session_scope() as session:
        result = await session.scalars(
            select(Chat.chat_id).where(Chat.digest_enabled.is_(True), Chat.blocked_at.is_(None))
        )
        return list(result)


async def top_discounts_recent(hours: int = 24, limit: int = 10) -> list[SaleSnapshot]:
    """Последний снэпшот на каждую игру за период (не все подряд — снэпшот пишется на
    каждом цикле опроса, пока скидка активна, иначе одна и та же игра засорила бы топ),
    отсортировано по размеру скидки."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    async with session_scope() as session:
        subq = (
            select(SaleSnapshot.appid, func.max(SaleSnapshot.id).label("max_id"))
            .where(SaleSnapshot.fetched_at >= since)
            .group_by(SaleSnapshot.appid)
            .subquery()
        )
        result = await session.execute(
            select(SaleSnapshot)
            .join(subq, SaleSnapshot.id == subq.c.max_id)
            .order_by(SaleSnapshot.discount_percent.desc())
            .limit(limit)
        )
        return list(result.scalars())


async def count_distinct_tracked_appids() -> int:
    async with session_scope() as session:
        return (await session.scalar(select(func.count(func.distinct(TrackedGame.appid))))) or 0


async def count_distinct_discounted_appids(hours: int) -> int:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    async with session_scope() as session:
        return (
            await session.scalar(
                select(func.count(func.distinct(SaleSnapshot.appid))).where(SaleSnapshot.fetched_at >= since)
            )
        ) or 0
