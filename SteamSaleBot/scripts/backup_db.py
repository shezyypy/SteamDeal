"""Бэкап Postgres в сжатый файл с таймстампом + удаление старых бэкапов сверх лимита.

Использование:
    python scripts/backup_db.py [--keep N]

Требует pg_dump в PATH (ставится вместе с Postgres — на Windows это обычно
C:\\Program Files\\PostgreSQL\\<версия>\\bin, добавь в PATH или вызови по полному пути).

Планировать через cron (Linux) или Планировщик заданий Windows, например раз в сутки.
Файлы кладутся в backups/ рядом с проектом — эта папка в .gitignore, бэкапы с реальными
данными пользователей не должны попадать в git."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Скрипт лежит в scripts/, а не в корне проекта — без этого "from app..." не находится.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import require, settings  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def _connection_parts(database_url: str) -> tuple[str, int, str, str, str]:
    # postgresql+asyncpg://user:pass@host:port/dbname -> обычный libpq DSN для pg_dump
    parsed = urlparse(database_url.replace("+asyncpg", ""))
    return (
        parsed.hostname or "localhost",
        parsed.port or 5432,
        parsed.username or "",
        parsed.password or "",
        parsed.path.lstrip("/"),
    )


def main(keep: int) -> None:
    if keep < 1:
        raise SystemExit("--keep должен быть не меньше 1")
    require("database_url")
    BACKUP_DIR.mkdir(exist_ok=True)

    host, port, user, password, dbname = _connection_parts(settings.database_url)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = BACKUP_DIR / f"steam_sale_bot_{timestamp}.sql"
    gz_path = raw_path.with_suffix(".sql.gz")

    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    args = [
        "pg_dump",
        "--no-owner",
        "--format=plain",
        f"--host={host}",
        f"--port={port}",
        f"--username={user}",
        dbname,
    ]

    with open(raw_path, "wb") as f:
        result = subprocess.run(args, stdout=f, stderr=subprocess.PIPE, env=env)

    if result.returncode != 0:
        raw_path.unlink(missing_ok=True)
        print(f"pg_dump завершился с ошибкой:\n{result.stderr.decode(errors='replace')}", file=sys.stderr)
        raise SystemExit(1)

    with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw_path.unlink()

    print(f"Бэкап сохранён: {gz_path}")

    backups = sorted(
        BACKUP_DIR.glob("steam_sale_bot_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in backups[keep:]:
        old.unlink()
        print(f"Удалён старый бэкап (сверх лимита {keep}): {old}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=int, default=7, help="сколько последних бэкапов хранить (по умолчанию 7)")
    args = parser.parse_args()
    main(args.keep)
