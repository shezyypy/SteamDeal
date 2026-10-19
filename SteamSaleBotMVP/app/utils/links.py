from __future__ import annotations

import urllib.parse
from html import escape


def steam_app_link(appid: int, name: str) -> str:
    """Кликабельная ссылка на страницу игры в Steam для HTML-сообщений Telegram."""
    return f'<a href="https://store.steampowered.com/app/{appid}">{escape(name)}</a>'


def market_item_link(appid: int, market_hash_name: str) -> str:
    """Кликабельная ссылка на страницу предмета на Steam Community Market.
    URL и видимый текст экранируются раздельно — это разные контексты (атрибут href
    и содержимое тега), экранирование одного не заменяет экранирование другого."""
    url = f"https://steamcommunity.com/market/listings/{appid}/{urllib.parse.quote(market_hash_name)}"
    return f'<a href="{escape(url)}">{escape(market_hash_name)}</a>'
