from scripts.preflight import validate_production_env


def _valid_env() -> dict[str, str]:
    return {
        "BOT_TOKEN": "configured",
        "DATABASE_URL": "postgresql+asyncpg://user:password@db:5432/app",
        "STEAM_API_KEY": "configured",
        "DATA_ENCRYPTION_KEY": "configured",
        "WEBAPP_MODE": "production",
        "WEBAPP_AUTH_REQUIRED": "1",
        "WEBAPP_URL": "https://steam.example.com",
        "BOT_USERNAME": "SteamDealBot",
        "PRIVACY_URL": "https://steam.example.com/privacy",
        "TERMS_URL": "https://steam.example.com/terms",
        "SUPPORT_URL": "https://steam.example.com/support",
        "REDIS_URL": "redis://redis:6379/0",
    }


def test_valid_production_environment_passes():
    assert validate_production_env(_valid_env()) == []


def test_missing_secret_is_reported_without_echoing_value():
    env = _valid_env()
    env["BOT_TOKEN"] = "super-secret-token"
    del env["BOT_TOKEN"]
    errors = validate_production_env(env)
    assert any("BOT_TOKEN" in error for error in errors)
    assert "super-secret-token" not in " ".join(errors)


def test_insecure_urls_and_disabled_auth_are_rejected():
    env = _valid_env()
    env.update(WEBAPP_URL="http://steam.example.com", WEBAPP_AUTH_REQUIRED="0", SUPPORT_URL="support")
    errors = validate_production_env(env)
    assert "WEBAPP_AUTH_REQUIRED must be enabled" in errors
    assert "WEBAPP_URL must be a valid HTTPS URL" in errors
    assert "SUPPORT_URL must be a valid HTTPS URL" in errors


def test_demo_configuration_is_not_accepted_as_production():
    env = _valid_env()
    env["WEBAPP_MODE"] = "demo"
    assert "WEBAPP_MODE must be production" in validate_production_env(env)
