from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.services import notifier


class FakeTrackedGame:
    def __init__(self, appid: int, name: str) -> None:
        self.appid = appid
        self.name = name


def _patched(stack: ExitStack, **overrides) -> None:
    defaults = dict(
        fetch_prices=AsyncMock(),
        chats_tracking_appid_in_region=AsyncMock(return_value=[(1, 0)]),
        already_notified=AsyncMock(return_value=False),
        list_tracked_games=AsyncMock(return_value=[FakeTrackedGame(440, "Team Fortress 2")]),
        log_notification=AsyncMock(),
        is_all_time_low=AsyncMock(return_value=False),
        is_chat_premium=AsyncMock(return_value=False),
        mark_chat_blocked=AsyncMock(),
        _record_snapshot=AsyncMock(),
    )
    defaults.update(overrides)

    mocks = {}
    for name, mock in defaults.items():
        target = f"app.services.notifier.{name}" if name in ("fetch_prices", "_record_snapshot") else (
            f"app.services.notifier.repository.{name}"
        )
        mocks[name] = stack.enter_context(patch(target, mock))
    return mocks


@pytest.mark.asyncio
async def test_skips_chats_already_notified_for_same_discount():
    bot = AsyncMock()
    sale = {
        "appid": 440,
        "price_original_cents": 100000,
        "price_final_cents": 50000,
        "discount_percent": 50,
        "currency": "RUB",
    }

    with ExitStack() as stack:
        _patched(
            stack,
            fetch_prices=AsyncMock(return_value=[sale]),
            already_notified=AsyncMock(return_value=True),
        )
        await notifier.check_and_notify(bot, [440], "ru")

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notifies_and_logs_new_discount():
    bot = AsyncMock()
    sale = {
        "appid": 440,
        "price_original_cents": 100000,
        "price_final_cents": 50000,
        "discount_percent": 50,
        "currency": "RUB",
    }

    with ExitStack() as stack:
        mocks = _patched(stack, fetch_prices=AsyncMock(return_value=[sale]))
        await notifier.check_and_notify(bot, [440], "ru")

    mocks["fetch_prices"].assert_awaited_once_with([440], cc="ru")
    bot.send_message.assert_awaited_once()
    mocks["log_notification"].assert_awaited_once_with(1, 440, 50)
    mocks["_record_snapshot"].assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_chat_below_its_own_discount_threshold():
    bot = AsyncMock()
    sale = {
        "appid": 440,
        "price_original_cents": 100000,
        "price_final_cents": 80000,
        "discount_percent": 20,
        "currency": "RUB",
    }

    # порог этого чата — 50%, а скидка всего 20%, уведомления быть не должно
    with ExitStack() as stack:
        _patched(
            stack,
            fetch_prices=AsyncMock(return_value=[sale]),
            chats_tracking_appid_in_region=AsyncMock(return_value=[(1, 50)]),
        )
        await notifier.check_and_notify(bot, [440], "ru")

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_appends_all_time_low_note_for_premium_chat_only():
    bot = AsyncMock()
    sale = {
        "appid": 440,
        "price_original_cents": 100000,
        "price_final_cents": 50000,
        "discount_percent": 50,
        "currency": "RUB",
    }

    with ExitStack() as stack:
        _patched(
            stack,
            fetch_prices=AsyncMock(return_value=[sale]),
            is_all_time_low=AsyncMock(return_value=True),
            is_chat_premium=AsyncMock(return_value=True),
        )
        await notifier.check_and_notify(bot, [440], "ru")

    sent_text = bot.send_message.await_args.args[1]
    assert "самая низкая цена" in sent_text.lower()


@pytest.mark.asyncio
async def test_marks_chat_blocked_on_forbidden_error_instead_of_crashing():
    bot = AsyncMock()
    forbidden_error = TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"), message="Forbidden: bot was blocked by the user"
    )
    bot.send_message.side_effect = forbidden_error

    sale = {
        "appid": 440,
        "price_original_cents": 100000,
        "price_final_cents": 50000,
        "discount_percent": 50,
        "currency": "RUB",
    }

    with ExitStack() as stack:
        mocks = _patched(stack, fetch_prices=AsyncMock(return_value=[sale]))
        await notifier.check_and_notify(bot, [440], "ru")

    mocks["mark_chat_blocked"].assert_awaited_once_with(1)
    mocks["log_notification"].assert_not_called()
