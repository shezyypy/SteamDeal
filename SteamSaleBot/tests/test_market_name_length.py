from unittest.mock import AsyncMock

import pytest

from app.handlers.market import _create_alert


@pytest.mark.asyncio
async def test_rejects_oversized_market_hash_name_gracefully():
    long_name = "x" * 300
    link = f"https://steamcommunity.com/market/listings/730/{long_name}"
    message = AsyncMock()

    await _create_alert(message, link, "below", "5.0")

    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.args[0]
    assert "слишком длинное" in sent_text.lower()
