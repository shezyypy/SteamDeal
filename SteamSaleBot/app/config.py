from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все значения читаются из .env (см. .env.example в корне проекта)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str | None = None
    admin_chat_id: int | None = None

    # SQLAlchemy async URL, например:
    # postgresql+asyncpg://steam_bot:PASSWORD@localhost:5432/steam_sale_bot
    database_url: str | None = None

    # redis://localhost:6379/0 — опционально, без него FSM хранится в памяти процесса
    redis_url: str | None = None

    # обязателен для поиска игр и импорта вишлиста, см. README.md
    steam_api_key: str | None = None

    # Ключ шифрования персональных данных в БД (сейчас — users.steam_id64), см. README.md
    # "Безопасность". Сгенерировать: python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"
    data_encryption_key: str | None = None

    sale_poll_interval_minutes: int = 5
    premium_poll_interval_minutes: int = 1
    market_poll_interval_minutes: int = 5  # реже, чем игры — Market API жёстче лимитирует запросы
    catalog_refresh_hours: int = 24

    premium_price_stars: int = 99
    premium_days: int = 30

    free_game_limit: int = 15  # безлимит — Premium

    # Консервативные потолки скорости исходящих запросов к Steam (см. app/services/rate_limit.py) —
    # не документированы официально Valve, подобраны по опыту сообщества, настраивай при 429.
    steam_pricing_rate_limit_per_minute: int = 30
    steam_market_rate_limit_per_minute: int = 15

    # Минимальный интервал между сообщениями от одного чата — простая защита от спама
    # командами (см. app/middlewares/throttling.py).
    throttle_interval_seconds: float = 0.7

    # Опционально: DSN проекта на sentry.io (или self-hosted) — без него мониторинг ошибок
    # просто не включается, бот работает как раньше, только логи остаются только в консоли.
    sentry_dsn: str | None = None

    # HTTPS-адрес Telegram Mini App. Telegram не принимает обычный localhost/http URL.
    webapp_url: str | None = None
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8787
    bot_username: str | None = None
    privacy_url: str | None = None
    terms_url: str | None = None
    support_url: str | None = None

    trial_days: int = 3  # бесплатный пробный Premium, один раз на чат — /trial
    referral_reward_days: int = 7  # награда рефереру за первого приглашённого, добавившего игру

    digest_interval_hours: int = 24  # как часто слать дайджест топ-скидок опт-ин чатам
    # Если скидка одновременно у доли отслеживаемых игр не меньше этого порога — считаем,
    # что началась крупная сезонная распродажа, и добавляем баннер в дайджест (не чаще
    # раза в неделю, чтобы не спамить одним и тем же баннером все 2 недели распродажи).
    seasonal_alert_threshold: float = 0.3


settings = Settings()


def require(*field_names: str) -> None:
    """Явно проверяет, что критичные для запуска настройки заполнены,
    и печатает понятную инструкцию вместо невнятного исключения из pydantic/psycopg."""

    missing = [name for name in field_names if getattr(settings, name) is None]
    if missing:
        raise RuntimeError(
            "Не заполнены обязательные настройки: "
            + ", ".join(missing)
            + ".\nСкопируйте .env.example в .env и заполните значения — см. раздел "
            "'Настройка окружения' в README.md."
        )
