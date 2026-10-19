from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Профиль конкретного человека в Telegram: используется для атрибуции в /feedback и
    для запоминания привязанного Steam-аккаунта (импорт вишлиста). Не путать с Chat —
    один и тот же человек может писать боту и в личке, и в нескольких группах сразу."""

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Хранится в зашифрованном виде (app/services/crypto.py) — отсюда Text, а не короткий
    # String(32): шифротекст (Fernet) заметно длиннее исходных 17 цифр steamid64.
    steam_id64: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chat(Base):
    """И личка, и группа/супергруппа — это Chat. Для личных чатов chat_id совпадает с tg_id
    пользователя (так уже устроено в Telegram), поэтому расширение на группы не потребовало
    отдельной модели данных для отслеживаемых игр — только смены смысла 'владельца' списка
    с 'пользователь' на 'чат'."""

    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String(20))  # private / group / supergroup / channel
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # premium_until — единственный источник правды о премиуме: NULL или дата в прошлом = free.
    # Отдельного bool-флага нет специально, чтобы не рассинхронизировать два поля.
    premium_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_region: Mapped[str] = mapped_column(String(8), nullable=False, server_default="ru")

    # Premium-фильтр: не уведомлять о скидках меньше этого процента. 0 = уведомлять обо всех.
    min_discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Ставится, когда send_message падает с TelegramForbiddenError (чат заблокировал бота) —
    # такие чаты исключаются из циклов опроса, чтобы не жечь rate-limit бюджет впустую.
    # Автоматически снимается в get_or_create_chat, как только чат снова пишет боту.
    blocked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # /trial — бесплатный пробный Premium один раз на чат. Не bool, а timestamp: заодно
    # видно, когда именно им воспользовались (полезно для аналитики).
    trial_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Опт-ин на дайджест топ-скидок — намеренно выключен по умолчанию, чтобы не превращать
    # рассылку в спам без явного согласия.
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Реферальная программа: кто пригласил этот чат, и выдана ли уже награда за него.
    # Награда — за первую добавленную игру приглашённым, а не за сам факт /start,
    # чтобы нельзя было накрутить рефералов пустыми чатами.
    referred_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="SET NULL"), nullable=True
    )
    referral_rewarded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tracked_games: Mapped[list["TrackedGame"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class TrackedGame(Base):
    __tablename__ = "tracked_games"
    __table_args__ = (UniqueConstraint("chat_id", "appid", name="uq_chat_game"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    appid: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(256))

    # Снэпшот на момент добавления — только для отображения "добавил(а) @username" в /list
    # групповых чатов, не для авторизации/прав.
    added_by_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_by_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    target_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="tracked_games")


class SaleSnapshot(Base):
    """История цен: одна строка на игру за каждый цикл опроса, где найдена скидка.
    Используется командой /history (Premium)."""

    __tablename__ = "sale_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(256))
    price_original_cents: Mapped[int] = mapped_column(Integer)
    price_final_cents: Mapped[int] = mapped_column(Integer)
    discount_percent: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrackedMarketItem(Base):
    """Ценовой алерт по предмету с Steam Community Market (скины, кейсы, карточки) —
    Premium-фича для трейдеров. В отличие от игр, у предметов нет 'исходной цены со
    скидкой', только плавающая рыночная цена, поэтому модель другая: разовый порог
    (below/above), а не процент скидки. Срабатывает один раз и удаляется — как
    лимитная заявка, а не повторяющаяся подписка."""

    __tablename__ = "tracked_market_items"
    __table_args__ = (
        UniqueConstraint("chat_id", "market_hash_name", "direction", name="uq_chat_item_direction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    appid: Mapped[int] = mapped_column(Integer)
    market_hash_name: Mapped[str] = mapped_column(String(256))
    direction: Mapped[str] = mapped_column(String(10))  # 'below' — упадёт ниже, 'above' — вырастет выше
    threshold_cents: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="price")
    pct: Mapped[float | None] = mapped_column(nullable=True)
    base_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    added_by_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_by_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkerHeartbeat(Base):
    """Отметка 'воркер реально прожил цикл N' — по одной строке на job_name планировщика.
    Используется /admin_stats, чтобы отличить 'воркер молчит, потому что нечего слать' от
    'воркер упал и никто не заметит, пока пользователи не начнут жаловаться'."""

    __tablename__ = "worker_heartbeats"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_run_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class NotificationLog(Base):
    """Не даёт слать одному чату уведомление про одну и ту же распродажу повторно на
    каждом цикле опроса — уникальность по (чат, игра, размер скидки)."""

    __tablename__ = "notification_log"
    __table_args__ = (
        UniqueConstraint("chat_id", "appid", "discount_percent", name="uq_notification"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    appid: Mapped[int] = mapped_column(Integer, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    """Идемпотентный журнал Telegram Stars-платежей."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("telegram_payment_charge_id", name="uq_payment_telegram_charge"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_payload: Mapped[str] = mapped_column(String(256), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    premium_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MiniAppEvent(Base):
    """Durable per-chat notification feed for the Mini App."""

    __tablename__ = "miniapp_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    price: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MarketPriceSnapshot(Base):
    """Durable observed prices for the Mini App Market charts."""

    __tablename__ = "market_price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    market_hash_name: Mapped[str] = mapped_column(String(256), index=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
