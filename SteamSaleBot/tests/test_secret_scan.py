from scripts.check_secrets import find_secret_matches


def test_detects_telegram_token_without_echoing_value():
    token = "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "abcdefghi"
    assert "Telegram bot token" in find_secret_matches(f"BOT_TOKEN={token}")


def test_detects_private_key_header():
    header = "-----BEGIN " + "PRIVATE KEY-----"
    assert "private key" in find_secret_matches(header)


def test_allows_empty_and_documented_example_placeholders():
    assert find_secret_matches("BOT_TOKEN=\nPOSTGRES_PASSWORD=change_me", ".env.example") == []


def test_detects_non_placeholder_credential_assignment():
    assert "credential assignment" in find_secret_matches("STEAM_API_KEY=actual-secret-value")
