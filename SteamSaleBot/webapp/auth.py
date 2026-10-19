"""Telegram Mini App initData validation.

The browser is not a trusted client.  Every production API request must carry
the initData string supplied by Telegram WebApp; the signature is verified
against the bot token before the request reaches a state-changing handler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class TelegramWebAppUser:
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86_400,
    now: int | None = None,
) -> TelegramWebAppUser | None:
    """Validate Telegram WebApp initData and return its user, if valid."""
    if not init_data or not bot_token:
        return None

    raw_pairs = parse_qsl(init_data, keep_blank_values=True)
    if len({key for key, _ in raw_pairs}) != len(raw_pairs):
        return None
    pairs = dict(raw_pairs)
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
        current_time = int(time.time()) if now is None else now
        if auth_date <= 0 or current_time - auth_date > max_age_seconds or auth_date - current_time > 60:
            return None
        raw_user = json.loads(pairs["user"])
        user_id = int(raw_user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    return TelegramWebAppUser(
        id=user_id,
        username=raw_user.get("username"),
        first_name=raw_user.get("first_name"),
        last_name=raw_user.get("last_name"),
    )
