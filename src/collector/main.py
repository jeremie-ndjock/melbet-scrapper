"""Point d'entrée de production : lance l'ordonnanceur permanent pour toutes les ligues
configurées, jusqu'à un signal d'arrêt (Ctrl+C, ``docker stop``) ou un blocage du site.

Usage : ``DATABASE_URL=... python -m collector.main``
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

import asyncpg

from .config import load_leagues, load_settings
from .scheduler import Scheduler
from .transport.http import HttpClient
from .transport.ratelimit import CircuitBreaker, RateLimiter

log = logging.getLogger("collector.main")


async def amain() -> int:
    settings = load_settings()
    leagues = {league.id: league.name for league in load_leagues()}
    t = settings.transport

    db_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=10)
    http = HttpClient(
        t.base_url, t.user_agent, timeout_seconds=t.timeout_seconds,
        rate_limiter=RateLimiter(t.max_requests_per_second),
        circuit_breaker=CircuitBreaker(t.circuit_breaker.failure_threshold, t.circuit_breaker.recovery_seconds),
        retry=t.retry,
    )
    # Même identité honnête pour le CDN, mais un débit et un coupe-circuit indépendants : ce n'est
    # pas le même service, et une lenteur du CDN ne doit pas ralentir la collecte des cotes.
    cdn_http = HttpClient(
        settings.dictionary.base_url, t.user_agent, timeout_seconds=t.timeout_seconds,
        rate_limiter=RateLimiter(t.max_requests_per_second),
        circuit_breaker=CircuitBreaker(t.circuit_breaker.failure_threshold, t.circuit_breaker.recovery_seconds),
        retry=t.retry,
    )

    scheduler = Scheduler(
        http=http, cdn_http=cdn_http, db_pool=db_pool, leagues=leagues,
        site_params=settings.site_params, poll_interval=settings.poll_interval_seconds,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, scheduler.stop)
        except NotImplementedError:
            # Windows ne supporte pas add_signal_handler ; acceptable en développement local,
            # la production (Docker sur Linux, voir docs/architecture.md) le supporte pleinement.
            signal.signal(sig, lambda *_: scheduler.stop())

    log.info("démarrage : %d ligue(s), User-Agent=%r, cycle=%.0fs", len(leagues), t.user_agent, settings.poll_interval_seconds)
    try:
        await scheduler.run(
            dictionary_refresh_interval=settings.dictionary.refresh_interval_hours * 3600,
            reconcile_interval=settings.results.reconcile_interval_minutes * 60,
            backfill_days=settings.results.backfill_days,
        )
    finally:
        await http.aclose()
        await cdn_http.aclose()
        await db_pool.close()

    return 3 if scheduler.metrics.blocked else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
