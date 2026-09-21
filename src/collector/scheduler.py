"""Ordonnanceur permanent : plusieurs ligues collectées en parallèle, une file bornée vers
l'écriture, tâches périodiques (dictionnaire, réconciliation des résultats), reprise complète
après redémarrage.

Architecture (voir docs/architecture.md, §4) :

    poll_loop(ligue 1) ──┐
    poll_loop(ligue 2) ──┼──► queue (bornée) ──► write_loop (un seul, séquentiel par ligue)
    ...                  ┘

Les boucles de sondage (réseau) et l'écriture (base) sont découplées par une file bornée : si
l'écriture prend du retard, la file se remplit et les producteurs sont mis en attente
(``queue.put`` bloque), ce qui ralentit naturellement la collecte plutôt que de perdre des données
ou de saturer la base. Chaque méthode ``*_once`` est un pas isolé, appelable directement dans les
tests sans dépendre du minutage réel ; ``run`` les enchaîne en boucles permanentes.

Politique de blocage (voir Memoire.md, section 20) : un ``BlockedError`` arrête **tout**
l'ordonnanceur (toutes les ligues partagent la même identité et la même adresse IP, un blocage les
concerne donc probablement toutes). Aucune tentative de contournement, aucun changement d'identité.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from .dedupe import ChangeDetector
from .dictionary import MarketDictionary, refresh_unlabeled_markets
from .pipeline import CycleResult, preload_from_db, process_cycle
from .results import backfill_results, reconcile_recent
from .sources.v3.client import fetch_games_by_champ
from .sources.v3.models import GamesByChampResponse
from .transport.errors import BlockedError, ParserError, ServerError
from .transport.http import HttpClient

log = logging.getLogger("collector.scheduler")


@dataclass(frozen=True)
class CycleJob:
    league_id: int
    response: GamesByChampResponse
    collected_at: datetime
    latency_ms: int


@dataclass
class SchedulerMetrics:
    """Compteurs en mémoire. Une exposition Prometheus réelle est prévue à l'étape 8 ;
    en attendant, ces compteurs permettent déjà d'observer le comportement en test et en
    exploitation manuelle (journaux)."""
    cycles: int = 0
    rows_written: int = 0
    errors: int = 0
    blocked: bool = False           # blocage du site principal : arrête tout l'ordonnanceur
    dictionary_blocked: bool = False  # blocage du CDN : arrête seulement le rafraîchissement des libellés


class Scheduler:
    def __init__(
        self,
        *,
        http: HttpClient,
        cdn_http: HttpClient,
        db_pool: asyncpg.Pool,
        leagues: dict[int, str],
        site_params: dict[str, object],
        poll_interval: float,
        queue_maxsize: int = 100,
    ):
        self.http = http
        self.cdn_http = cdn_http
        self.db_pool = db_pool
        self.leagues = leagues
        self.site_params = site_params
        self.poll_interval = poll_interval
        self.queue: asyncio.Queue[CycleJob] = asyncio.Queue(maxsize=queue_maxsize)
        self.detectors: dict[int, ChangeDetector] = {league_id: ChangeDetector() for league_id in leagues}
        self.dictionary = MarketDictionary()
        self.metrics = SchedulerMetrics()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Demande un arrêt propre : les boucles en cours terminent leur pas courant, la file
        d'écriture est vidée, puis les tâches se terminent."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------ démarrage / reprise

    async def preload_all(self) -> None:
        """Recharge l'état des sélections déjà connues pour chaque ligue (reprise après
        redémarrage). Une ligue en erreur transitoire au démarrage ne bloque pas les autres : son
        détecteur restera vide et traitera son tout premier relevé normalement (comme un premier
        lancement). Un blocage, en revanche, arrête tout l'ordonnanceur (voir ``_stop_if_blocked``) :
        il n'y a pas de raison de croire que les autres ligues répondraient différemment."""
        for league_id in self.leagues:
            try:
                response, _ = await fetch_games_by_champ(self.http, league_id, self.site_params)
            except BlockedError as exc:
                self._stop_if_blocked(exc, context=f"préchargement, ligue {league_id}")
                return
            except (ServerError, ParserError) as exc:
                log.warning("préchargement impossible pour la ligue %s (%s) : le premier cycle s'en chargera", league_id, exc)
                continue
            async with self.db_pool.acquire() as conn:
                await preload_from_db(conn, self.detectors[league_id], [g.id for g in response.games])

    def _stop_if_blocked(self, exc: BlockedError, *, context: str) -> None:
        log.critical("BLOQUÉ par le site (%s) : %s — arrêt de l'ordonnanceur, aucun contournement", context, exc)
        self.metrics.blocked = True
        self.stop()

    # ------------------------------------------------------------------ pas unitaires (testables)

    async def poll_once(self, league_id: int) -> bool:
        """Un cycle de sondage pour une ligue : récupère et met en file. Retourne ``False`` si un
        blocage a été détecté (l'appelant doit alors cesser d'appeler cette méthode)."""
        try:
            response, latency_ms = await fetch_games_by_champ(self.http, league_id, self.site_params)
        except BlockedError as exc:
            self._stop_if_blocked(exc, context=f"sondage, ligue {league_id}")
            return False
        except (ServerError, ParserError) as exc:
            log.error("cycle ignoré pour la ligue %s (%s) : %s", league_id, type(exc).__name__, exc)
            self.metrics.errors += 1
            return True
        await self.queue.put(CycleJob(league_id, response, datetime.now(timezone.utc), latency_ms))
        return True

    async def write_once(self, *, timeout: float = 1.0) -> CycleResult | None:
        """Traite un job de la file s'il y en a un dans le délai imparti. Retourne ``None`` si la
        file était vide (permet à l'appelant de revérifier une condition d'arrêt sans bloquer)."""
        try:
            job = await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None
        try:
            async with self.db_pool.acquire() as conn:
                result = await process_cycle(
                    conn, self.detectors[job.league_id], job.response,
                    league_id=job.league_id, collected_at=job.collected_at, latency_ms=job.latency_ms,
                )
            self.metrics.cycles += 1
            self.metrics.rows_written += result.n_rows_written
            return result
        finally:
            self.queue.task_done()

    async def refresh_dictionary_once(self) -> int:
        """Un blocage du CDN n'arrête que le rafraîchissement des libellés : ce n'est pas le même
        service que le site principal, et son absence ne fait que priver les nouveaux marchés de
        libellé lisible (dégradé, non fatal pour la collecte des cotes elle-même)."""
        try:
            async with self.db_pool.acquire() as conn:
                return await refresh_unlabeled_markets(self.cdn_http, conn, self.dictionary)
        except BlockedError as exc:
            log.critical("BLOQUÉ par le CDN du dictionnaire : %s — ce rafraîchissement cesse, la collecte des cotes continue", exc)
            self.metrics.dictionary_blocked = True
            return 0

    async def reconcile_results_once(self) -> int:
        total = 0
        for league_id in self.leagues:
            if self.stopping:
                break
            try:
                async with self.db_pool.acquire() as conn:
                    total += await reconcile_recent(self.http, conn, league_id, self.site_params)
            except BlockedError as exc:
                self._stop_if_blocked(exc, context=f"réconciliation des résultats, ligue {league_id}")
                return total
        return total

    async def backfill_once(self, *, days_back: int) -> int:
        total = 0
        for league_id in self.leagues:
            if self.stopping:
                break
            try:
                async with self.db_pool.acquire() as conn:
                    total += await backfill_results(
                        self.http, conn, league_id, self.site_params, days_back=days_back,
                        should_stop=lambda: self.stopping,  # arrêt réactif entre deux fenêtres
                    )
            except BlockedError as exc:
                self._stop_if_blocked(exc, context=f"rattrapage des résultats, ligue {league_id}")
                return total
        return total

    # ------------------------------------------------------------------ boucles permanentes

    async def _poll_loop(self, league_id: int) -> None:
        while not self.stopping:
            t0 = time.monotonic()
            if not await self.poll_once(league_id):
                return  # blocage : self.stop() a déjà été appelé par poll_once
            await self._sleep_until_next_cycle(t0)

    async def _sleep_until_next_cycle(self, cycle_started_at: float) -> None:
        remaining = self.poll_interval - (time.monotonic() - cycle_started_at)
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=remaining)
        except (asyncio.TimeoutError, TimeoutError):
            pass  # délai écoulé normalement : cycle suivant

    async def _write_loop(self) -> None:
        while not self.stopping or not self.queue.empty():
            await self.write_once(timeout=0.5)

    async def _periodic(self, action, interval_seconds: float, name: str, *, should_continue=lambda: True) -> None:
        while not self.stopping and should_continue():
            try:
                await action()
            except Exception:
                log.exception("tâche périodique %r en échec, nouvelle tentative au prochain intervalle", name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_seconds)
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def run(self, *, dictionary_refresh_interval: float, reconcile_interval: float, backfill_days: int) -> None:
        """Boucle permanente : reprise, rattrapage initial, puis collecte et tâches périodiques
        jusqu'à l'arrêt (``stop()``, ou un blocage détecté par une des boucles)."""
        await self.preload_all()
        if not self.stopping:  # un blocage détecté au préchargement rend un rattrapage inutile
            await self.backfill_once(days_back=backfill_days)  # rattrapage profond, une fois au démarrage

        tasks = [
            asyncio.create_task(self._write_loop(), name="write_loop"),
            *(asyncio.create_task(self._poll_loop(league_id), name=f"poll_loop[{league_id}]") for league_id in self.leagues),
            asyncio.create_task(self._periodic(
                self.refresh_dictionary_once, dictionary_refresh_interval, "dictionary_refresh",
                should_continue=lambda: not self.metrics.dictionary_blocked,
            ), name="dictionary_refresh"),
            asyncio.create_task(self._periodic(self.reconcile_results_once, reconcile_interval, "reconcile_results"), name="reconcile_results"),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
