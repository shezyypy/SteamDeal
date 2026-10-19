import aiohttp
import pytest

from app.config import settings
from app.services import steam_catalog
from app.services.steam_catalog import SteamCatalogUnavailable, extract_appid_from_link


def test_extract_appid_from_plain_link():
    assert extract_appid_from_link("https://store.steampowered.com/app/1091500/Cyberpunk_2077/") == 1091500


def test_extract_appid_from_link_without_slug():
    assert extract_appid_from_link("store.steampowered.com/app/440") == 440


def test_extract_appid_returns_none_for_non_link():
    assert extract_appid_from_link("Half-Life 2") is None


@pytest.mark.asyncio
async def test_resolve_by_name_raises_clear_error_without_api_key(monkeypatch):
    # Регрессия: без STEAM_API_KEY поиск игры не должен молча падать неясным исключением —
    # см. разбор в conversation, IStoreService/GetAppList/v1 требует ключ.
    monkeypatch.setattr(settings, "steam_api_key", None)
    monkeypatch.setattr(steam_catalog, "_name_to_appid", None)
    monkeypatch.setattr(steam_catalog, "_appid_to_name", None)

    async with aiohttp.ClientSession() as session:
        with pytest.raises(SteamCatalogUnavailable):
            await steam_catalog.resolve_appid_by_name(session, "Half-Life 2")
