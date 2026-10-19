import hashlib
import hmac
import json
from urllib.parse import urlencode

from webapp.auth import validate_init_data
from aiohttp.test_utils import TestClient, TestServer
import webapp.server as webapp_server


def _init_data(bot_token: str, auth_date: int = 1_700_000_000) -> str:
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": 123, "username": "buyer"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_init_data_returns_user():
    user = validate_init_data(_init_data("bot-token", 1_700_000_000), "bot-token", now=1_700_000_100)
    assert user is not None
    assert user.id == 123
    assert user.username == "buyer"


def test_invalid_signature_is_rejected():
    assert validate_init_data(_init_data("bot-token") + "x", "bot-token", now=1_700_000_100) is None


def test_expired_init_data_is_rejected():
    assert validate_init_data(_init_data("bot-token", 1_700_000_000), "bot-token", now=1_700_100_000) is None


def test_init_data_is_rejected_after_one_hour_by_default():
    assert validate_init_data(_init_data("bot-token", 1_700_000_000), "bot-token", now=1_700_003_601) is None


def test_duplicate_init_data_keys_are_rejected():
    assert validate_init_data(_init_data("bot-token") + "&auth_date=1", "bot-token", now=1_700_000_100) is None


async def test_api_rejects_missing_telegram_auth(monkeypatch):
    monkeypatch.setattr(webapp_server, "WEBAPP_AUTH_REQUIRED", True)
    monkeypatch.setattr(webapp_server, "TELEGRAM_BOT_TOKEN", "bot-token")
    api = webapp_server.ApiServer()
    app = api.build_app()
    # Do not start Steam or the demo background loop in this middleware test.
    app.on_startup.clear()
    app.on_cleanup.clear()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/session")
        assert response.status == 401
        assert (await response.json())["error"] == "telegram_auth_required"
    finally:
        await client.close()


async def test_healthz_is_public_but_has_security_headers(monkeypatch):
    monkeypatch.setattr(webapp_server, "WEBAPP_MODE", "demo")
    monkeypatch.setattr(webapp_server, "WEBAPP_AUTH_REQUIRED", False)
    api = webapp_server.ApiServer()
    app = api.build_app()
    app.on_startup.clear()
    app.on_cleanup.clear()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/healthz")
        assert response.status == 200
        assert (await response.json())["ok"] is True
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "Content-Security-Policy" in response.headers
        assert response.headers["Cache-Control"] == "no-cache, must-revalidate"
    finally:
        await client.close()
