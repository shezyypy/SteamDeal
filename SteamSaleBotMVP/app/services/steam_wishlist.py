from __future__ import annotations

import re

import aiohttp

from app.config import settings

# ВАЖНО: старый AJAX-эндпоинт store.steampowered.com/wishlist/profiles/<id>/wishlistdata/
# Valve тоже убрал (сейчас редиректит на витрину магазина вместо JSON). Рабочая замена —
# IWishlistService/GetWishlist/v1, не требует ключа. Проверено вживую: возвращает
# {"response": {}} и для несуществующего, и для приватного, и для пустого вишлиста —
# API не различает эти случаи, поэтому и сообщение пользователю ниже сформулировано через "либо".
_WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
_RESOLVE_VANITY_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"

_STEAMID64_RE = re.compile(r"^\d{17}$")
_PROFILE_LINK_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_VANITY_LINK_RE = re.compile(r"steamcommunity\.com/id/([^/\s?]+)")


class SteamWishlistUnavailable(Exception):
    """Не удалось получить вишлист — нет ключа, профиль не резолвится, или Steam не ответил."""


async def resolve_steamid64(session: aiohttp.ClientSession, text: str) -> str | None:
    """Принимает steamid64, ссылку /profiles/<id>/ или /id/<vanity>/, либо голый vanity-ник."""
    text = text.strip()

    if _STEAMID64_RE.match(text):
        return text

    match = _PROFILE_LINK_RE.search(text)
    if match:
        return match.group(1)

    match = _VANITY_LINK_RE.search(text)
    vanity = match.group(1) if match else text
    return await _resolve_vanity(session, vanity)


async def _resolve_vanity(session: aiohttp.ClientSession, vanity: str) -> str | None:
    if not settings.steam_api_key:
        raise SteamWishlistUnavailable(
            "STEAM_API_KEY не задан в .env — без него не резолвятся ссылки вида /id/ВАШ_НИК/."
        )

    params = {"key": settings.steam_api_key, "vanityurl": vanity}
    try:
        async with session.get(
            _RESOLVE_VANITY_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except aiohttp.ClientError as exc:
        raise SteamWishlistUnavailable(f"не удалось связаться со Steam ({exc})") from exc

    result = data.get("response", {})
    if result.get("success") == 1:
        return result.get("steamid")
    return None


async def fetch_wishlist_appids(session: aiohttp.ClientSession, steamid64: str) -> list[int]:
    params = {"steamid": steamid64}
    try:
        async with session.get(
            _WISHLIST_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                raise SteamWishlistUnavailable(f"Steam вернул статус {resp.status}")
            data = await resp.json()
    except aiohttp.ClientError as exc:
        raise SteamWishlistUnavailable(f"не удалось связаться со Steam ({exc})") from exc

    items = data.get("response", {}).get("items", [])
    return [item["appid"] for item in items if "appid" in item]
