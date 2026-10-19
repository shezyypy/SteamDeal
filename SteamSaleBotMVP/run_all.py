"""Запуск Telegram-бота, worker и Mini App из одной папки проекта.

Mini App из Arena пока работает в demo-режиме на JSON, а бот и worker используют
PostgreSQL. Процессы запускаются вместе, но их данные не смешиваются до отдельной
миграции DemoStore на репозитории бота.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV = os.environ.copy()


def start(command: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, cwd=cwd, env=ENV)


def main() -> int:
    processes = [
        start([sys.executable, "main.py"], ROOT),
        start([sys.executable, "worker.py"], ROOT),
        start([sys.executable, "server.py"], ROOT / "webapp"),
    ]
    try:
        return_code = 0
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    return_code = code or 0
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
