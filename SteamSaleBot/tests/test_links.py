from app.utils.links import market_item_link, steam_app_link


def test_steam_app_link_escapes_name():
    result = steam_app_link(440, "Team & Fortress <2>")
    assert result == '<a href="https://store.steampowered.com/app/440">Team &amp; Fortress &lt;2&gt;</a>'


def test_market_item_link_quotes_url_and_escapes_text():
    result = market_item_link(730, "Chroma 2 Case")
    assert result == (
        '<a href="https://steamcommunity.com/market/listings/730/Chroma%202%20Case">'
        "Chroma 2 Case</a>"
    )


def test_market_item_link_handles_special_characters_in_name():
    # Ножи/StatTrak-предметы часто содержат ★, |, кавычки и т.п. — не должны ломать HTML/URL.
    result = market_item_link(730, 'StatTrak™ AK-47 | "Redline"')
    assert 'href="https://steamcommunity.com/market/listings/730/' in result
    assert "&quot;" in result  # кавычки в видимом тексте экранированы для HTML
    assert '"Redline"' not in result.split(">", 1)[1]  # сырые кавычки не просочились в текст ссылки
