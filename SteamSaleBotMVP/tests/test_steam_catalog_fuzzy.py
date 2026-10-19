from app.services.steam_catalog import _find_close_matches


def test_substring_match_prioritized_and_sorted_by_length():
    names = ["Half-Life 2: Episode One", "Half-Life 2", "Half-Life", "Portal 2"]
    result = _find_close_matches("half-life", names, limit=5)
    assert result[0] == "Half-Life"  # самое короткое подстрочное совпадение — первым
    assert "Portal 2" not in result


def test_falls_back_to_fuzzy_when_no_substring_match():
    names = ["Team Fortress 2", "Counter-Strike 2", "Dota 2"]
    result = _find_close_matches("Team Fortres 2", names, limit=5)  # опечатка
    assert result == ["Team Fortress 2"]


def test_returns_empty_when_nothing_close():
    names = ["Team Fortress 2", "Counter-Strike 2", "Dota 2"]
    assert _find_close_matches("xyzxyzxyz completely unrelated", names, limit=5) == []
