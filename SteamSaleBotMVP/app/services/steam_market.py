from __future__ import annotations

import re
import urllib.parse

import aiohttp

_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"
_MARKET_LINK_RE = re.compile(r"steamcommunity\.com/market/listings/(\d+)/([^/?#]+)")

# Steam Market принимает числовой код валюты (ECurrencyCode), а не строковый cc-код,
# как store-api. Сопоставлено с кодами регионов из /region и проверено вживую по реальным
# ответам priceoverview (currency=5 -> "руб.", currency=1 -> "$", и т.д.).
_REGION_TO_CURRENCY_ID = {
    "ru": 5,
    "us": 1,
    "ua": 18,
    "kz": 37,
    "tr": 17,
    "de": 3,
    "gb": 2,
}

# Захватывает именно числовой фрагмент (начинается и заканчивается цифрой, между ними
# могут быть цифры/пробелы/точки/запятые) — а не "всё, что не буква", иначе в '437,72 руб.'
# в остаток попадала бы и точка из 'руб.', ломая поиск разделителя копеек в конце строки.
_NUMBER_SPAN_RE = re.compile(r"\d[\d\s.,]*\d|\d")
_TRAILING_CENTS_RE = re.compile(r"[.,](\d{2})$")


class SteamMarketUnavailable(Exception):
    """Не удалось получить цену предмета с торговой площадки."""


def extract_market_item_from_link(text: str) -> tuple[int, str] | None:
    """Достаёт (appid, market_hash_name) из ссылки вида
    https://steamcommunity.com/market/listings/730/Chroma%202%20Case"""
    match = _MARKET_LINK_RE.search(text)
    if not match:
        return None
    appid = int(match.group(1))
    market_hash_name = urllib.parse.unquote(match.group(2))
    return appid, market_hash_name


def _parse_price_to_cents(price_str: str) -> int | None:
    """Steam отдаёт цену строкой с символом валюты в разных позициях и с разным
    разделителем дробной части ('$5.72', '437,72 руб.', '5,01€', '2 693,72₸') —
    проверено вживую на реальных ответах API для нескольких валют."""
    span_match = _NUMBER_SPAN_RE.search(price_str)
    if not span_match:
        return None

    number = span_match.group(0).replace(" ", "").replace("\xa0", "")

    decimal_match = _TRAILING_CENTS_RE.search(number)
    if decimal_match:
        integer_part = re.sub(r"[.,]", "", number[: decimal_match.start()]) or "0"
        fractional_part = decimal_match.group(1)
    else:
        integer_part = re.sub(r"[.,]", "", number) or "0"
        fractional_part = "00"

    return int(integer_part) * 100 + int(fractional_part)


async def fetch_market_price_cents(
    session: aiohttp.ClientSession, appid: int, market_hash_name: str, region: str
) -> int | None:
    currency_id = _REGION_TO_CURRENCY_ID.get(region, 5)
    params = {
        "appid": str(appid),
        "currency": str(currency_id),
        "market_hash_name": market_hash_name,
    }
    try:
        async with session.get(
            _PRICE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                raise SteamMarketUnavailable(f"Steam Market вернул статус {resp.status}")
            data = await resp.json()
    except aiohttp.ClientError as exc:
        raise SteamMarketUnavailable(f"не удалось связаться со Steam Market ({exc})") from exc

    if not data.get("success"):
        return None

    price_str = data.get("lowest_price") or data.get("median_price")
    if not price_str:
        return None

    return _parse_price_to_cents(price_str)
