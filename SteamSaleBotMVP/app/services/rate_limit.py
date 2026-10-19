from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Ограничитель 'не больше max_calls запросов за period_seconds', общий на все вызовы
    к конкретному Steam-эндпоинту в рамках процесса. В отличие от asyncio.Semaphore (которая
    только ограничивает, сколько запросов идёт ОДНОВРЕМЕННО), это ограничивает реальную
    СКОРОСТЬ запросов независимо от того, сколько корутин их ждут — если игр/предметов
    станет больше, цикл опроса просто растянется по времени, а не пробьёт лимит.

    У Steam нет официально документированных лимитов на эти эндпоинты — значения ниже
    подобраны консервативно по опыту сообщества (см. комментарии в местах использования)
    и их стоит уменьшить, если на практике всё равно будут прилетать 429."""

    def __init__(self, max_calls: int, period_seconds: float) -> None:
        self._max_calls = max_calls
        self._period = period_seconds
        self._lock = asyncio.Lock()
        self._call_times: list[float] = []

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._call_times = [t for t in self._call_times if now - t < self._period]
                if len(self._call_times) < self._max_calls:
                    self._call_times.append(now)
                    return
                sleep_for = self._period - (now - self._call_times[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
