"""Small load test: many people browsing at once. Reports speed and errors per endpoint.

Usage (inside the api container, against a copy of the API with rate limits off):
  RATE_LIMIT_ENABLED=false uvicorn app.main:app --port 8001 &
  python ops/loadtest.py http://127.0.0.1:8001 --users 40 --seconds 20
Never point this at the live site: rate limits (correctly) block it and it would slow real guests.
"""
import argparse
import asyncio
import statistics
import time

import httpx

FAILS: dict[str, int] = {}
PATHS = ["/api/v1/health", "/api/v1/health/ready", "/api/v1/search?limit=20", "/api/v1/meta/listing", "/api/v1/legal/terms"]


async def worker(client: httpx.AsyncClient, until: float, results: dict[str, list]):
    i = 0
    while time.monotonic() < until:
        path = PATHS[i % len(PATHS)]
        i += 1
        t = time.perf_counter()
        try:
            r = await client.get(path)
            code = r.status_code
        except Exception as e:
            code = 0
            FAILS[type(e).__name__] = FAILS.get(type(e).__name__, 0) + 1
        results.setdefault(path, []).append((time.perf_counter() - t, code))


async def main(base: str, users: int, seconds: int):
    results: dict[str, list] = {}
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        until = time.monotonic() + seconds
        started = time.monotonic()
        await asyncio.gather(*[worker(client, until, results) for _ in range(users)])
        elapsed = time.monotonic() - started
    total = errors = 0
    print(f"{users} users for {elapsed:.0f}s")
    print(f"{'endpoint':32} {'requests':>8} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} {'errors':>7}")
    for path, rows in results.items():
        times = sorted(t * 1000 for t, _ in rows)
        bad = sum(1 for _, c in rows if c == 0 or c >= 500 or c == 429)
        total += len(rows)
        errors += bad
        print(f"{path:32} {len(rows):8d} {statistics.median(times):8.0f} {times[int(len(times) * .95) - 1]:8.0f} {times[-1]:8.0f} {bad:7d}")
    if FAILS:
        print("failures by kind:", FAILS)
    print(f"total {total} requests, {total / elapsed:.0f}/s, {errors} errors")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("--users", type=int, default=30)
    p.add_argument("--seconds", type=int, default=15)
    a = p.parse_args()
    asyncio.run(main(a.base, a.users, a.seconds))
