import datetime as dt
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.services import digest


class FakeSnapshot:
    def __init__(self, appid: int, name: str, discount_percent: int, price_final_cents: int) -> None:
        self.appid = appid
        self.name = name
        self.discount_percent = discount_percent
        self.price_final_cents = price_final_cents
        self.currency = "RUB"


def _patched(stack: ExitStack, **overrides):
    defaults = dict(
        record_heartbeat=AsyncMock(),
        top_discounts_recent=AsyncMock(return_value=[]),
        count_distinct_tracked_appids=AsyncMock(return_value=0),
        count_distinct_discounted_appids=AsyncMock(return_value=0),
        get_heartbeats=AsyncMock(return_value={}),
        chats_with_digest_enabled=AsyncMock(return_value=[]),
        mark_chat_blocked=AsyncMock(),
    )
    defaults.update(overrides)
    return {
        name: stack.enter_context(patch(f"app.services.digest.repository.{name}", mock))
        for name, mock in defaults.items()
    }


@pytest.mark.asyncio
async def test_sends_nothing_when_no_discounts_and_no_seasonal_signal():
    bot = AsyncMock()
    with ExitStack() as stack:
        _patched(stack)
        await digest.send_daily_digest(bot)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_sends_top_discounts_to_opted_in_chats():
    bot = AsyncMock()
    snapshots = [FakeSnapshot(440, "Team Fortress 2", 50, 50000)]

    with ExitStack() as stack:
        _patched(
            stack,
            top_discounts_recent=AsyncMock(return_value=snapshots),
            chats_with_digest_enabled=AsyncMock(return_value=[111, 222]),
        )
        await digest.send_daily_digest(bot)

    assert bot.send_message.await_count == 2
    sent_text = bot.send_message.await_args_list[0].args[1]
    assert "Team Fortress 2" in sent_text
    assert "50%" in sent_text


@pytest.mark.asyncio
async def test_marks_chat_blocked_on_forbidden_error():
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=SendMessage(chat_id=111, text="x"), message="blocked"
    )
    snapshots = [FakeSnapshot(440, "Team Fortress 2", 50, 50000)]

    with ExitStack() as stack:
        mocks = _patched(
            stack,
            top_discounts_recent=AsyncMock(return_value=snapshots),
            chats_with_digest_enabled=AsyncMock(return_value=[111]),
        )
        await digest.send_daily_digest(bot)

    mocks["mark_chat_blocked"].assert_awaited_once_with(111)


@pytest.mark.asyncio
async def test_seasonal_banner_triggers_above_threshold():
    bot = AsyncMock()
    with ExitStack() as stack:
        _patched(
            stack,
            count_distinct_tracked_appids=AsyncMock(return_value=10),
            count_distinct_discounted_appids=AsyncMock(return_value=5),  # 50% > порог 0.3
            chats_with_digest_enabled=AsyncMock(return_value=[111]),
        )
        await digest.send_daily_digest(bot)

    sent_text = bot.send_message.await_args.args[1]
    assert "крупная распродажа" in sent_text.lower()


@pytest.mark.asyncio
async def test_seasonal_banner_suppressed_if_recently_alerted():
    bot = AsyncMock()
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)

    with ExitStack() as stack:
        _patched(
            stack,
            count_distinct_tracked_appids=AsyncMock(return_value=10),
            count_distinct_discounted_appids=AsyncMock(return_value=5),
            get_heartbeats=AsyncMock(return_value={"seasonal_alert_sent": recent}),
            chats_with_digest_enabled=AsyncMock(return_value=[111]),
        )
        await digest.send_daily_digest(bot)

    # баннера про распродажу быть не должно (недавно уже предупреждали), а обычного топа
    # тоже нет (top_discounts_recent пуст по умолчанию) — значит, рассылки не будет вовсе
    bot.send_message.assert_not_called()
