import asyncio
import time

import pytest

from app.services.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_allows_burst_up_to_max_calls_without_delay():
    limiter = RateLimiter(max_calls=3, period_seconds=60)
    start = time.monotonic()

    for _ in range(3):
        await limiter.acquire()

    assert time.monotonic() - start < 0.5


@pytest.mark.asyncio
async def test_blocks_until_period_elapses_once_limit_reached():
    limiter = RateLimiter(max_calls=2, period_seconds=0.3)

    await limiter.acquire()
    await limiter.acquire()

    start = time.monotonic()
    await limiter.acquire()  # третий вызов должен подождать, пока не "истечёт" первый
    elapsed = time.monotonic() - start

    assert elapsed >= 0.2  # с запасом от 0.3, чтобы не флапать на медленном CI


@pytest.mark.asyncio
async def test_serializes_concurrent_callers():
    limiter = RateLimiter(max_calls=1, period_seconds=0.2)
    order: list[int] = []

    async def worker(n: int) -> None:
        await limiter.acquire()
        order.append(n)

    await asyncio.gather(worker(1), worker(2), worker(3))

    assert sorted(order) == [1, 2, 3]
