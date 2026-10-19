from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def init_sentry(process_name: str) -> None:
    """Опционально — активна, только если задан SENTRY_DSN в .env. Собственных вызовов
    capture_exception по коду почти нет: sentry_sdk по умолчанию подключает интеграцию
    с logging и сама превращает наши logger.exception(...) (их уже много по коду) в события
    Sentry — этого достаточно, не нужно было переписывать обработку ошибок повсюду."""

    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN не задан — мониторинг ошибок выключен для %s", process_name)
        return

    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, environment=process_name, traces_sample_rate=0.0)
    logger.info("Sentry подключен для %s", process_name)
