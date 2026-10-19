from __future__ import annotations

import asyncio
import difflib
import re
import time

import aiohttp

from app.config import settings

# ВАЖНО: старый бесплатный `ISteamApps/GetAppList/v2` (без ключа) Valve отключил —
# сервер отвечает 404 "Method 'GetAppList' not found in interface 'ISteamApps'".
# Рабочая замена — `IStoreService/GetAppList/v1`, но она требует `key`
# (бесплатный Steam Web API ключ: https://steamcommunity.com/dev/apikey,
# вписать в .env как STEAM_API_KEY — см. README.md).
_APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
_LINK_PATTERN = re.compile(r"store\.steampowered\.com/app/(\d+)")
_CACHE_TTL_SECONDS = 24 * 60 * 60
_PAGE_SIZE = 50000

_name_to_appid: dict[str, int] | None = None
_appid_to_name: dict[int, str] | None = None
_cache_loaded_at: float = 0.0


class SteamCatalogUnavailable(Exception):
    """Поиск игры по названию/ссылке/ID недоступен — нет STEAM_API_KEY или Steam не ответил."""


def extract_appid_from_link(text: str) -> int | None:
    """Достаёт appid из ссылки вида https://store.steampowered.com/app/1091500/..."""
    match = _LINK_PATTERN.search(text)
    return int(match.group(1)) if match else None


async def _load_catalog(session: aiohttp.ClientSession) -> None:
    """Тянет полный список игр (без DLC/софта/видео) постранично через last_appid.
    ~100k+ игр за несколько запросов по _PAGE_SIZE — справочник имя <-> appid,
    не список именно игр со скидками."""
    global _name_to_appid, _appid_to_name, _cache_loaded_at

    if _name_to_appid is not None and time.monotonic() - _cache_loaded_at < _CACHE_TTL_SECONDS:
        return

    if not settings.steam_api_key:
        raise SteamCatalogUnavailable(
            "STEAM_API_KEY не задан в .env. Получи бесплатный ключ на "
            "https://steamcommunity.com/dev/apikey и пропиши его в .env."
        )

    name_to_appid: dict[str, int] = {}
    appid_to_name: dict[int, str] = {}
    last_appid = 0

    while True:
        params = {
            "key": settings.steam_api_key,
            "include_games": "true",
            "include_dlc": "false",
            "include_software": "false",
            "include_videos": "false",
            "include_hardware": "false",
            "max_results": str(_PAGE_SIZE),
            "last_appid": str(last_appid),
        }
        try:
            async with session.get(
                _APP_LIST_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    raise SteamCatalogUnavailable(
                        f"Steam вернул статус {resp.status} при запросе списка игр — "
                        "проверь, что STEAM_API_KEY в .env правильный."
                    )
                payload = await resp.json()
        except aiohttp.ClientError as exc:
            raise SteamCatalogUnavailable(f"Не удалось связаться со Steam: {exc}") from exc

        response = payload.get("response", {})
        apps = response.get("apps", [])
        for app in apps:
            name = app.get("name")
            appid = app.get("appid")
            if name and appid is not None:
                name_to_appid[name] = appid
                appid_to_name[appid] = name

        if not response.get("have_more_results") or not apps:
            break
        last_appid = response.get("last_appid", apps[-1]["appid"])

    _name_to_appid = name_to_appid
    _appid_to_name = appid_to_name
    _cache_loaded_at = time.monotonic()


async def resolve_appid_by_name(session: aiohttp.ClientSession, name: str) -> int | None:
    """Точное совпадение по названию, как в оригинальном id_parser.py.
    Если точного нет — см. suggest_matches ниже (нечёткий поиск для подсказок)."""
    await _load_catalog(session)
    return _name_to_appid.get(name)


def _find_close_matches(text: str, names: list[str], limit: int) -> list[str]:
    lowered = text.lower()
    substring_matches = [n for n in names if lowered in n.lower()]
    if substring_matches:
        substring_matches.sort(key=len)  # короткие/точнее совпадающие названия — выше
        return substring_matches[:limit]
    return difflib.get_close_matches(text, names, n=limit, cutoff=0.6)


async def suggest_matches(session: aiohttp.ClientSession, text: str, limit: int = 5) -> list[tuple[int, str]]:
    """Когда точного совпадения по названию нет — предлагает похожие варианты (сначала по
    подстроке, затем по нечёткому сходству через difflib), чтобы пользователю не пришлось
    подбирать точный регистр/пунктуацию вручную. CPU-работа на ~100k+ названий уводится в
    отдельный поток (run_in_executor), чтобы не блокировать event loop бота для всех остальных
    чатов на время поиска."""
    await _load_catalog(session)
    names = list(_name_to_appid.keys())

    loop = asyncio.get_event_loop()
    matched_names = await loop.run_in_executor(None, _find_close_matches, text, names, limit)
    return [(_name_to_appid[matched_name], matched_name) for matched_name in matched_names]


async def resolve_name_by_appid(session: aiohttp.ClientSession, appid: int) -> str | None:
    """Настоящее название игры из каталога Steam — используется, чтобы в /list и в
    уведомлениях показывалось название игры, а не то, что пользователь ввёл руками
    (ссылка или голый ID)."""
    await _load_catalog(session)
    return _appid_to_name.get(appid)
