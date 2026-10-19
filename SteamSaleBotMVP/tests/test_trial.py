import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

from app.handlers.premium import cmd_trial


class FakeChat:
    def __init__(self, premium_until=None, trial_used_at=None) -> None:
        self.premium_until = premium_until
        self.trial_used_at = trial_used_at


def _make_message(chat_id: int = 111):
    message = AsyncMock()
    message.chat.id = chat_id
    return message


@pytest.mark.asyncio
async def test_trial_grants_when_never_used():
    message = _make_message()

    with (
        patch("app.handlers.premium.repository.get_chat", AsyncMock(return_value=FakeChat())),
        patch("app.handlers.premium.repository.grant_trial", AsyncMock(return_value=True)) as grant_trial,
    ):
        await cmd_trial(message)

    grant_trial.assert_awaited_once()
    assert "активирован" in message.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_trial_rejected_if_already_used():
    message = _make_message()
    chat = FakeChat(trial_used_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10))

    with (
        patch("app.handlers.premium.repository.get_chat", AsyncMock(return_value=chat)),
        patch("app.handlers.premium.repository.grant_trial", AsyncMock()) as grant_trial,
    ):
        await cmd_trial(message)

    grant_trial.assert_not_called()
    assert "уже был использован" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_trial_rejected_if_already_premium():
    message = _make_message()
    chat = FakeChat(premium_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5))

    with (
        patch("app.handlers.premium.repository.get_chat", AsyncMock(return_value=chat)),
        patch("app.handlers.premium.repository.grant_trial", AsyncMock()) as grant_trial,
    ):
        await cmd_trial(message)

    grant_trial.assert_not_called()
    assert "уже есть premium" in message.answer.await_args.args[0].lower()
