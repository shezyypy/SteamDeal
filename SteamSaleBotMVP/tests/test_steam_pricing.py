from unittest.mock import patch

import pytest

from app.services.steam_pricing import fetch_price, fetch_prices


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def get(self, *args, **kwargs):
        return _FakeResponse(self._payload, self._status)


@pytest.mark.asyncio
async def test_fetch_price_handles_empty_list_data_without_crashing():
    # Регрессия: Steam иногда отдаёт "success": true, "data": [] (пустой список вместо
    # словаря) для игр, недоступных в конкретном регионе — раньше это роняло весь цикл
    # проверки скидок AttributeError'ом на .get() у списка.
    session = _FakeSession({"440": {"success": True, "data": []}})
    result = await fetch_price(session, 440, "ru")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_price_returns_none_when_no_discount():
    session = _FakeSession(
        {"440": {"success": True, "data": {"price_overview": {"initial": 1000, "final": 1000, "discount_percent": 0, "currency": "RUB"}}}}
    )
    result = await fetch_price(session, 440, "ru")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_price_parses_discount():
    session = _FakeSession(
        {"440": {"success": True, "data": {"price_overview": {"initial": 1000, "final": 500, "discount_percent": 50, "currency": "RUB"}}}}
    )
    result = await fetch_price(session, 440, "ru")
    assert result == {
        "appid": 440,
        "price_original_cents": 1000,
        "price_final_cents": 500,
        "discount_percent": 50,
        "currency": "RUB",
    }


@pytest.mark.asyncio
async def test_fetch_price_returns_none_on_rate_limit_response():
    session = _FakeSession({}, status=429)
    result = await fetch_price(session, 440, "ru")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_prices_skips_broken_appid_without_failing_the_batch():
    # Регрессия: одна игра с "плохой" формой ответа не должна валить всю пачку —
    # именно это уронило запланированную задачу в проде (см. traceback в разговоре).
    async def fake_fetch_price(session, appid, cc="ru"):
        if appid == 999:
            raise AttributeError("'list' object has no attribute 'get'")
        return {
            "appid": appid,
            "price_original_cents": 1000,
            "price_final_cents": 500,
            "discount_percent": 50,
            "currency": "RUB",
        }

    with patch("app.services.steam_pricing.fetch_price", fake_fetch_price):
        results = await fetch_prices([440, 999, 570], cc="ru")

    assert sorted(r["appid"] for r in results) == [440, 570]
