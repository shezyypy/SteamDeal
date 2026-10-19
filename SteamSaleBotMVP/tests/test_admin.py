import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.handlers.admin import (
    IsAdmin,
    cmd_admin_blocked,
    cmd_admin_broadcast,
    cmd_admin_premium,
    cmd_admin_revoke,
    cmd_admin_stats,
)


class FakeChat:
    def __init__(self, chat_id: int, chat_type: str = "private", blocked_at=None) -> None:
        self.chat_id = chat_id
        self.chat_type = chat_type
        self.blocked_at = blocked_at


@pytest.fixture(autouse=True)
def _admin_chat_id(monkeypatch):
    monkeypatch.setattr(settings, "admin_chat_id", 999)
    yield


def _make_message(chat_id: int):
    message = AsyncMock()
    message.chat.id = chat_id
    return message


@pytest.mark.asyncio
async def test_is_admin_true_only_for_configured_admin_chat():
    is_admin = IsAdmin()
    assert await is_admin(_make_message(999)) is True
    assert await is_admin(_make_message(111)) is False


@pytest.mark.asyncio
async def test_is_admin_false_when_admin_chat_id_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_chat_id", None)
    is_admin = IsAdmin()
    assert await is_admin(_make_message(999)) is False


@pytest.mark.asyncio
async def test_admin_stats_reports_stale_worker():
    stats = {
        "total_chats": 10,
        "private_chats": 7,
        "group_chats": 3,
        "premium_chats": 2,
        "blocked_chats": 1,
        "total_tracked_games": 42,
        "total_market_alerts": 5,
        "heartbeats": {
            "check_sales_free": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=60),
        },
    }
    message = _make_message(999)

    with patch("app.handlers.admin.repository.admin_stats", AsyncMock(return_value=stats)):
        await cmd_admin_stats(message)

    sent_text = message.answer.await_args.args[0]
    assert "10" in sent_text
    assert "воркер не работает" in sent_text  # check_sales_free просрочен (60 мин > 5*3)
    assert "ещё ни разу не запускался" in sent_text  # check_sales_premium/market вообще без записи


@pytest.mark.asyncio
async def test_admin_premium_rejects_unknown_chat():
    message = _make_message(999)
    command = AsyncMock(args="12345 30")

    with patch("app.handlers.admin.repository.get_chat", AsyncMock(return_value=None)) as get_chat:
        await cmd_admin_premium(message, command)

    get_chat.assert_awaited_once_with(12345)
    assert "не известен" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_admin_premium_grants_for_known_chat():
    message = _make_message(999)
    command = AsyncMock(args="12345 30")

    with (
        patch("app.handlers.admin.repository.get_chat", AsyncMock(return_value=FakeChat(12345))),
        patch("app.handlers.admin.repository.grant_premium", AsyncMock()) as grant_premium,
    ):
        await cmd_admin_premium(message, command)

    grant_premium.assert_awaited_once()
    assert grant_premium.await_args.args[0] == 12345


@pytest.mark.asyncio
async def test_admin_revoke_requires_numeric_chat_id():
    message = _make_message(999)
    command = AsyncMock(args="not-a-number")

    await cmd_admin_revoke(message, command)

    assert "Формат" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_admin_blocked_lists_chats():
    message = _make_message(999)
    blocked = [FakeChat(111, blocked_at=dt.datetime.now(dt.timezone.utc))]

    with patch("app.handlers.admin.repository.list_blocked_chats", AsyncMock(return_value=blocked)):
        await cmd_admin_blocked(message)

    assert "111" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_admin_broadcast_counts_sent_and_forbidden():
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    message = _make_message(999)
    command = AsyncMock(args="привет всем")

    async def fake_send_message(chat_id, text):
        if chat_id == 2:
            raise TelegramForbiddenError(method=SendMessage(chat_id=2, text=text), message="blocked")
        return None

    message.bot.send_message = AsyncMock(side_effect=fake_send_message)

    with (
        patch("app.handlers.admin.repository.all_chat_ids", AsyncMock(return_value=[1, 2, 3])),
        patch("app.handlers.admin.repository.mark_chat_blocked", AsyncMock()) as mark_blocked,
        patch("app.handlers.admin._broadcast_limiter.acquire", AsyncMock()),
    ):
        await cmd_admin_broadcast(message, command)

    mark_blocked.assert_awaited_once_with(2)
    final_text = message.answer.await_args_list[-1].args[0]
    assert "Разослано: 2" in final_text
    assert "не доставлено: 1" in final_text
