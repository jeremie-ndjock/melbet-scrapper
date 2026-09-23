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
from prometheus_client import start_http_server

from .alerting import AlertSender, ThrottledAlerter, load_alert_config_from_env
from .config import load_leagues, load_settings
from .live_feed import LiveFeedProcessor
from .observability.logging_setup import configure_logging
from .observability.metrics import REGISTRY
from .pre_match import PreMatchAnnouncer
from .scheduler import Scheduler
from .storage.migrate import migrate
from .telegram_feed import MatchFeedSender, load_match_feed_config_from_env
from .transport.http import HttpClient
from .transport.ratelimit import CircuitBreaker, RateLimiter

log = logging.getLogger("collector.main")


async def amain() -> int:
    settings = load_settings()
    all_leagues = load_leagues()
    leagues = {league.id: league.name for league in all_leagues}
    league_sport_ids = {league.id: league.sport_id for league in all_leagues}
    t = settings.transport

    alert_config = load_alert_config_from_env()
    if not alert_config.telegram_enabled and not alert_config.email_enabled:
        log.warning("aucun canal d'alerte configuré (.env) : les incidents ne seront visibles que dans les journaux")
    alerter = ThrottledAlerter(AlertSender(alert_config), cooldown_seconds=settings.alerting.cooldown_seconds)

    start_http_server(settings.observability.metrics_port, registry=REGISTRY)
    log.info("métriques exposées sur le port %d (/metrics)", settings.observability.metrics_port)

    # Applique les migrations en attente avant tout accès à la base : une base persistante (volume
    # Docker) créée à une étape antérieure du projet peut être en retard de plusieurs migrations.
    # Sans cela, le premier schéma qui change (ex. migration 005, égalités autorisées) provoquerait
    # une erreur en boucle au démarrage plutôt qu'une mise à niveau silencieuse et automatique.
    applied = await migrate(os.environ["DATABASE_URL"])
    if applied:
        log.info("migrations appliquées au démarrage : %s", ", ".join(applied))

    db_pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=2, max_size=10)
    http = HttpClient(
        t.base_url, t.user_agent, timeout_seconds=t.timeout_seconds,
        rate_limiter=RateLimiter(t.max_requests_per_second),
        circuit_breaker=CircuitBreaker(t.circuit_breaker.failure_threshold, t.circuit_breaker.recovery_seconds),
        retry=t.retry, source_label="melbet",
    )
    # Même identité honnête pour le CDN, mais un débit et un coupe-circuit indépendants : ce n'est
    # pas le même service, et une lenteur du CDN ne doit pas ralentir la collecte des cotes.
    cdn_http = HttpClient(
        settings.dictionary.base_url, t.user_agent, timeout_seconds=t.timeout_seconds,
        rate_limiter=RateLimiter(t.max_requests_per_second),
        circuit_breaker=CircuitBreaker(t.circuit_breaker.failure_threshold, t.circuit_breaker.recovery_seconds),
        retry=t.retry, source_label="cdn",
    )

    match_feed_config = load_match_feed_config_from_env()
    live_feed = None
    pre_match = None
    if match_feed_config.enabled:
        feed_sender = MatchFeedSender(match_feed_config.bot_token)
        live_feed = LiveFeedProcessor(
            http=http, site_params=settings.site_params,
            sender=feed_sender, chat_ids=match_feed_config.chat_ids,
        )
        pre_match = PreMatchAnnouncer(sender=feed_sender, chat_ids=match_feed_config.chat_ids)
        log.info("fil de match en direct activé (Telegram) pour %d ligue(s) : %s",
                 len(match_feed_config.chat_ids), sorted(match_feed_config.chat_ids))

    scheduler = Scheduler(
        http=http, cdn_http=cdn_http, db_pool=db_pool, leagues=leagues,
        league_sport_ids=league_sport_ids,
        site_params=settings.site_params, legacy_site_params=settings.legacy_site_params,
        poll_interval=settings.poll_interval_seconds, alerter=alerter,
        live_feed=live_feed, pre_match=pre_match,
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
    configure_logging()
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
