from app.handlers.premium import _is_expected_payment, _make_payload, _parse_payload


def test_make_and_parse_roundtrip():
    payload = _make_payload(111)
    assert _parse_payload(payload) == 111


def test_parse_rejects_wrong_prefix():
    assert _parse_payload("other:111") is None


def test_parse_rejects_non_numeric_suffix():
    assert _parse_payload("premium:abc") is None


def test_parse_rejects_empty_string():
    assert _parse_payload("") is None


def test_payment_amount_and_currency_must_match_tariff():
    from app.config import settings

    assert _is_expected_payment("XTR", settings.premium_price_stars)
    assert not _is_expected_payment("USD", settings.premium_price_stars)
    assert not _is_expected_payment("XTR", settings.premium_price_stars + 1)
