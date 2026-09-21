"""Prototype de collecte pour une ligue (étape 4) : boucle bornée, à usage manuel de démonstration.

L'ordonnanceur permanent multi-ligues, avec reprise complète après redémarrage, fait l'objet de
l'étape 6. Ce script se contente d'un nombre fixe de cycles, pour vérifier le pipeline en conditions
réelles avant de l'automatiser.

Comportement en cas de blocage (403/429) : le programme s'arrête immédiatement et signale l'erreur.
Il ne change jamais d'identité (User-Agent, en-têtes) pour continuer — voir Memoire.md, section
« anti-scraping ».

Usage : DATABASE_URL=... python -m collector.prototype --league 1252965 --cycles 5
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import asyncpg

from .config import load_leagues, load_settings
from .dedupe import ChangeDetector
from .pipeline import preload_from_db, process_cycle
from .sources.v3.client import fetch_games_by_champ
from .transport.errors import BlockedError, ParserError, ServerError
from .transport.http import HttpClient
from .transport.ratelimit import CircuitBreaker, RateLimiter

log = logging.getLogger("collector.prototype")


async def run(league_id: int, cycles: int) -> None:
    settings = load_settings()
    leagues = {league.id: league.name for league in load_leagues()}
    if league_id not in leagues:
        raise SystemExit(f"ligue inconnue dans config/leagues.yaml : {league_id}")

    t = settings.transport
    http = HttpClient(
        t.base_url,
        t.user_agent,
        timeout_seconds=t.timeout_seconds,
        rate_limiter=RateLimiter(t.max_requests_per_second),
        circuit_breaker=CircuitBreaker(t.circuit_breaker.failure_threshold, t.circuit_breaker.recovery_seconds),
        retry=t.retry,
    )
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    detector = ChangeDetector()
    poll_interval = settings.poll_interval_seconds

    log.info("démarrage : ligue %s (%s), %d cycle(s), User-Agent=%r", league_id, leagues[league_id], cycles, t.user_agent)
    try:
        for cycle in range(1, cycles + 1):
            t0 = time.monotonic()
            try:
                response, latency_ms = await fetch_games_by_champ(http, league_id, settings.site_params)
            except BlockedError as exc:
                log.error("BLOQUÉ par le site (%s) : arrêt immédiat, aucune tentative de contournement", exc)
                raise
            except (ServerError, ParserError) as exc:
                log.error("cycle %d échoué (%s) : %s", cycle, type(exc).__name__, exc)
                continue

            if response.games and cycle == 1:
                await preload_from_db(conn, detector, [g.id for g in response.games])

            result = await process_cycle(
                conn, detector, response, league_id=league_id,
                collected_at=datetime.now(timezone.utc), latency_ms=latency_ms,
            )
            log.info("cycle %d : %d match(s), %d ligne(s) de cotes écrites (%d ms)",
                      cycle, result.n_games, result.n_rows_written, latency_ms)

            elapsed = time.monotonic() - t0
            if cycle < cycles and elapsed < poll_interval:
                await asyncio.sleep(poll_interval - elapsed)
    finally:
        await http.aclose()
        await conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", type=int, required=True)
    parser.add_argument("--cycles", type=int, default=3)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.league, args.cycles))
    except BlockedError:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
