import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db import repository


@pytest.mark.asyncio
async def test_refund_payment_rolls_back_only_matching_entitlement():
    now = dt.datetime.now(dt.timezone.utc)
    payment = SimpleNamespace(
        telegram_payment_charge_id="charge-1",
        chat_id=42,
        refunded_at=None,
        premium_until_after=now + dt.timedelta(days=30),
        premium_until_before=None,
    )
    chat = SimpleNamespace(premium_until=payment.premium_until_after)
    session = AsyncMock()
    session.scalar.return_value = payment
    session.get.return_value = chat

    with patch.object(repository, "session_scope") as scope:
        scope.return_value.__aenter__ = AsyncMock(return_value=session)
        scope.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await repository.refund_payment("charge-1")

    assert result == (42, True)
    assert payment.refunded_at is not None
    assert chat.premium_until is None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refund_payment_keeps_later_purchase_entitlement():
    now = dt.datetime.now(dt.timezone.utc)
    payment = SimpleNamespace(
        telegram_payment_charge_id="charge-1",
        chat_id=42,
        refunded_at=None,
        premium_until_after=now + dt.timedelta(days=30),
        premium_until_before=None,
    )
    chat = SimpleNamespace(premium_until=now + dt.timedelta(days=60))
    session = AsyncMock()
    session.scalar.return_value = payment
    session.get.return_value = chat

    with patch.object(repository, "session_scope") as scope:
        scope.return_value.__aenter__ = AsyncMock(return_value=session)
        scope.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await repository.refund_payment("charge-1")

    assert result == (42, False)
    assert chat.premium_until == now + dt.timedelta(days=60)
    assert payment.refunded_at is not None
