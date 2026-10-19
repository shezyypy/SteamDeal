"""Validate deployment configuration without printing secret values."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv


_TRUTHY = {"1", "true", "yes", "on"}


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool((env.get(name) or "").strip())


def _https_url(env: Mapping[str, str], name: str) -> bool:
    value = (env.get(name) or "").strip()
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_production_env(env: Mapping[str, str]) -> list[str]:
    """Return configuration errors, never echoing the value of a setting."""
    errors: list[str] = []
    required = {
        "BOT_TOKEN": "Telegram bot token",
        "DATABASE_URL": "PostgreSQL connection URL",
        "STEAM_API_KEY": "Steam API key",
        "DATA_ENCRYPTION_KEY": "data encryption key",
        "WEBAPP_URL": "Mini App HTTPS URL",
        "BOT_USERNAME": "bot username",
        "PRIVACY_URL": "privacy URL",
        "TERMS_URL": "terms URL",
        "SUPPORT_URL": "support URL",
    }
    for name, label in required.items():
        if not _present(env, name):
            errors.append(f"{label} ({name}) is not configured")

    database_url = (env.get("DATABASE_URL") or "").strip()
    if database_url and not database_url.startswith("postgresql+asyncpg://"):
        errors.append("DATABASE_URL must use the postgresql+asyncpg scheme")

    mode = (env.get("WEBAPP_MODE") or "").strip().lower()
    if mode != "production":
        errors.append("WEBAPP_MODE must be production")
    if (env.get("WEBAPP_AUTH_REQUIRED") or "").strip().lower() not in _TRUTHY:
        errors.append("WEBAPP_AUTH_REQUIRED must be enabled")

    for name in ("WEBAPP_URL", "PRIVACY_URL", "TERMS_URL", "SUPPORT_URL"):
        if _present(env, name) and not _https_url(env, name):
            errors.append(f"{name} must be a valid HTTPS URL")

    if _present(env, "REDIS_URL") and not (env["REDIS_URL"].strip().startswith(("redis://", "rediss://"))):
        errors.append("REDIS_URL must use redis:// or rediss://")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SteamDeal production environment")
    parser.add_argument("--env-file", default=".env", help="dotenv file to load")
    args = parser.parse_args()
    load_dotenv(args.env_file)
    errors = validate_production_env(os.environ)
    if errors:
        print("Production preflight failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Production preflight passed: required configuration is present and safe to start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
