from app.services.steam_market import _parse_price_to_cents, extract_market_item_from_link

# Значения ниже — реальные ответы Steam Market priceoverview (проверено вживую для
# appid=730, "Chroma 2 Case", разные currency).


def test_parse_price_usd():
    assert _parse_price_to_cents("$5.72") == 572


def test_parse_price_rub():
    assert _parse_price_to_cents("437,72 руб.") == 43772


def test_parse_price_eur():
    assert _parse_price_to_cents("5,01€") == 501


def test_parse_price_gbp():
    assert _parse_price_to_cents("£4.28") == 428


def test_parse_price_try():
    assert _parse_price_to_cents("268,13 TL") == 26813


def test_parse_price_kzt_with_thousands_separator():
    assert _parse_price_to_cents("2 693,72₸") == 269372


def test_extract_market_item_from_link():
    result = extract_market_item_from_link(
        "https://steamcommunity.com/market/listings/730/Chroma%202%20Case"
    )
    assert result == (730, "Chroma 2 Case")


def test_extract_market_item_from_link_returns_none_for_non_link():
    assert extract_market_item_from_link("Chroma 2 Case") is None
