import aiohttp
import pytest

from app.services.steam_wishlist import resolve_steamid64


@pytest.mark.asyncio
async def test_resolve_steamid64_accepts_raw_id():
    async with aiohttp.ClientSession() as session:
        assert await resolve_steamid64(session, "76561197960287930") == "76561197960287930"


@pytest.mark.asyncio
async def test_resolve_steamid64_extracts_from_profiles_link():
    async with aiohttp.ClientSession() as session:
        result = await resolve_steamid64(
            session, "https://steamcommunity.com/profiles/76561197960287930/inventory"
        )
        assert result == "76561197960287930"
