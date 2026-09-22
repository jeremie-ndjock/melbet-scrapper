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

Politique de changement de structure (source dégradée) : quand la source principale (v3) échoue à
valider son schéma (``ParserError`` — le site a changé), la réponse brute est archivée
(``dead_letter``) et la ligue bascule immédiatement sur la source de secours (legacy) pour ne pas
perdre le cycle en cours. Elle y reste, puis retente périodiquement la source principale (toutes les
``recovery_probe_cycles`` boucles) pour détecter une éventuelle réparation, sans jamais s'arrêter :
un changement de structure dégrade, il ne bloque pas.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from .alerting import ThrottledAlerter
from .dedupe import ChangeDetector
from .dictionary import MarketDictionary, refresh_unlabeled_markets
from .live_feed import LiveFeedProcessor
from .observability import metrics
from .pipeline import CycleResult, preload_from_db, process_cycle
from .results import backfill_results, reconcile_recent
from .sources.legacy.client import fetch_games_by_champ_legacy
from .sources.v3.client import fetch_games_by_champ
from .sources.v3.models import GamesByChampResponse
from .storage import queries
from .storage.writer import SOURCE_LEGACY, SOURCE_V3
from .transport.errors import BlockedError, ParserError, ServerError
from .transport.http import HttpClient

# Nombre de cycles passés sur la source de secours avant de retenter la source principale.
DEFAULT_RECOVERY_PROBE_CYCLES = 12  # ~1 min à un cycle de 5 s
# Taille maximale du payload conservé en lettre morte (les réponses les plus grosses sont tronquées).
DEAD_LETTER_PAYLOAD_LIMIT = 20_000

log = logging.getLogger("collector.scheduler")


@dataclass(frozen=True)
class CycleJob:
    league_id: int
    response: GamesByChampResponse
    collected_at: datetime
    latency_ms: int
    source: int = SOURCE_V3


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
    schema_changes_detected: int = 0  # nombre de bascules vers la source de secours


class Scheduler:
    def __init__(
        self,
        *,
        http: HttpClient,
        cdn_http: HttpClient,
        db_pool: asyncpg.Pool,
        leagues: dict[int, str],
        site_params: dict[str, object],
        legacy_site_params: dict[str, object],
        poll_interval: float,
        queue_maxsize: int = 100,
        recovery_probe_cycles: int = DEFAULT_RECOVERY_PROBE_CYCLES,
        alerter: ThrottledAlerter | None = None,
        live_feed: LiveFeedProcessor | None = None,
    ):
        self.http = http
        self.cdn_http = cdn_http
        self.db_pool = db_pool
        self.leagues = leagues
        self.site_params = site_params
        self.legacy_site_params = legacy_site_params
        self.poll_interval = poll_interval
        self.recovery_probe_cycles = recovery_probe_cycles
        self.alerter = alerter
        self.live_feed = live_feed
        self.queue: asyncio.Queue[CycleJob] = asyncio.Queue(maxsize=queue_maxsize)
        self.detectors: dict[int, ChangeDetector] = {league_id: ChangeDetector() for league_id in leagues}
        self.dictionary = MarketDictionary()
        self.metrics = SchedulerMetrics()
        self._stop = asyncio.Event()
        self._degraded: set[int] = set()             # ligues actuellement sur la source de secours
        self._probe_countdown: dict[int, int] = {}    # cycles restants avant de retenter le v3

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
                await self._stop_if_blocked(exc, context=f"préchargement, ligue {league_id}")
                return
            except (ServerError, ParserError) as exc:
                log.warning("préchargement impossible pour la ligue %s (%s) : le premier cycle s'en chargera", league_id, exc)
                continue
            async with self.db_pool.acquire() as conn:
                await preload_from_db(conn, self.detectors[league_id], [g.id for g in response.games])

    async def _send_alert(self, key: str, subject: str, message: str) -> None:
        """Envoie une alerte si un ``ThrottledAlerter`` est configuré. Ne lève jamais : une alerte
        qui échoue est déjà journalisée par ``AlertSender``, elle ne doit jamais interrompre la
        collecte elle-même."""
        if self.alerter is not None:
            await self.alerter.alert(key, subject, message)

    async def _stop_if_blocked(self, exc: BlockedError, *, context: str) -> None:
        log.critical("BLOQUÉ par le site (%s) : %s — arrêt de l'ordonnanceur, aucun contournement", context, exc)
        self.metrics.blocked = True
        metrics.blocked_total.labels(source="melbet").inc()
        self.stop()
        # Attendue (pas « fire and forget ») : le programme s'arrête juste après, un envoi non
        # attendu risquerait d'être interrompu par la fermeture de la boucle d'événements avant
        # d'aboutir, alors que c'est justement l'alerte la plus importante à ne pas perdre.
        await self._send_alert(
            "blocked", "🛑 Collecteur bloqué",
            f"Le site a bloqué le collecteur ({context}) : {exc}\nL'ordonnanceur s'est arrêté, "
            "aucune tentative de contournement n'a été faite. Une intervention humaine est nécessaire.",
        )

    # ------------------------------------------------------------------ pas unitaires (testables)

    async def _fetch_via_legacy(self, league_id: int) -> tuple[GamesByChampResponse, int] | None:
        try:
            return await fetch_games_by_champ_legacy(self.http, league_id, self.leagues[league_id], self.legacy_site_params)
        except BlockedError as exc:
            await self._stop_if_blocked(exc, context=f"source de secours, ligue {league_id}")
            return None
        except ParserError as exc:
            metrics.parser_errors_total.labels(source="legacy", league=str(league_id)).inc()
            log.error("source de secours également en échec pour la ligue %s (%s) : %s", league_id, type(exc).__name__, exc)
            self.metrics.errors += 1
            await self._log_collection(league_id=league_id, source=SOURCE_LEGACY, endpoint="champzip+gamezip",
                                        ok=False, error=str(exc))
            return None
        except ServerError as exc:
            log.error("source de secours également en échec pour la ligue %s (%s) : %s", league_id, type(exc).__name__, exc)
            self.metrics.errors += 1
            await self._log_collection(league_id=league_id, source=SOURCE_LEGACY, endpoint="champzip+gamezip",
                                        ok=False, error=str(exc))
            return None

    async def _mark_degraded(self, league_id: int, exc: ParserError) -> None:
        metrics.parser_errors_total.labels(source="melbet", league=str(league_id)).inc()
        already_degraded = league_id in self._degraded
        self._degraded.add(league_id)
        self._probe_countdown[league_id] = self.recovery_probe_cycles
        payload = exc.payload[:DEAD_LETTER_PAYLOAD_LIMIT]
        async with self.db_pool.acquire() as conn:
            await conn.execute(queries.INSERT_DEAD_LETTER, 1, exc.endpoint, league_id, str(exc), payload, len(exc.payload))
        if not already_degraded:
            log.critical(
                "structure inattendue de la source principale pour la ligue %s (%s) : "
                "bascule sur la source de secours, nouvel essai dans %d cycle(s)",
                league_id, exc, self.recovery_probe_cycles,
            )
            self.metrics.schema_changes_detected += 1
            metrics.schema_changes_total.labels(league=str(league_id)).inc()
            await self._send_alert(
                f"degraded:{league_id}", f"⚠️ Source de secours activée (ligue {league_id})",
                f"La source principale ne correspond plus au schéma attendu pour la ligue {league_id} : "
                f"{exc}\nLa collecte continue via la source de secours (plus lente). "
                f"Un nouvel essai automatique aura lieu dans environ {self.recovery_probe_cycles} cycles.",
            )

    async def _log_collection(self, *, league_id: int, source: int, endpoint: str, ok: bool,
                               http_status: int | None = None, latency_ms: int | None = None,
                               n_games: int | None = None, n_rows_written: int | None = None,
                               error: str | None = None) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                queries.INSERT_COLLECTION_LOG, datetime.now(timezone.utc), league_id, source, endpoint,
                ok, http_status, latency_ms, n_games, n_rows_written, error,
            )

    async def poll_once(self, league_id: int) -> bool:
        """Un cycle de sondage pour une ligue : récupère et met en file. Retourne ``False`` si un
        blocage a été détecté (l'appelant doit alors cesser d'appeler cette méthode).

        Bascule automatiquement sur la source de secours si la source principale ne valide plus son
        schéma (changement de structure du site), et retente périodiquement la source principale
        une fois dégradée (voir le en-tête du module)."""
        probing_recovery = False
        if league_id in self._degraded:
            self._probe_countdown[league_id] -= 1
            probing_recovery = self._probe_countdown[league_id] <= 0

        source = SOURCE_V3
        if league_id not in self._degraded or probing_recovery:
            try:
                response, latency_ms = await fetch_games_by_champ(self.http, league_id, self.site_params)
            except BlockedError as exc:
                await self._stop_if_blocked(exc, context=f"sondage, ligue {league_id}")
                return False
            except ParserError as exc:
                await self._mark_degraded(league_id, exc)
                result = await self._fetch_via_legacy(league_id)
                if result is None:
                    return not self.stopping
                response, latency_ms = result
                source = SOURCE_LEGACY
            except ServerError as exc:
                log.error("cycle ignoré pour la ligue %s (%s) : %s", league_id, type(exc).__name__, exc)
                self.metrics.errors += 1
                await self._log_collection(league_id=league_id, source=SOURCE_V3, endpoint="gamesByChamp",
                                            ok=False, error=str(exc))
                return True
            else:
                if probing_recovery:
                    log.warning("source principale rétablie pour la ligue %s : fin de la dégradation", league_id)
                    self._degraded.discard(league_id)
                    if self.alerter is not None:
                        self.alerter.reset(f"degraded:{league_id}")  # une rechute sera signalée sans délai
                    await self._send_alert(
                        f"recovered:{league_id}", f"✅ Source principale rétablie (ligue {league_id})",
                        f"La ligue {league_id} n'utilise plus la source de secours : le site répond de "
                        "nouveau au format attendu.",
                    )
        else:
            result = await self._fetch_via_legacy(league_id)
            if result is None:
                return not self.stopping
            response, latency_ms = result
            source = SOURCE_LEGACY

        await self.queue.put(CycleJob(league_id, response, datetime.now(timezone.utc), latency_ms, source))
        return True

    async def write_once(self, *, timeout: float = 1.0) -> CycleResult | None:
        """Traite un job de la file s'il y en a un dans le délai imparti. Retourne ``None`` si la
        file était vide (permet à l'appelant de revérifier une condition d'arrêt sans bloquer)."""
        metrics.queue_depth.set(self.queue.qsize())
        try:
            job = await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None
        try:
            t0 = time.monotonic()
            async with self.db_pool.acquire() as conn:
                result = await process_cycle(
                    conn, self.detectors[job.league_id], job.response,
                    league_id=job.league_id, collected_at=job.collected_at, latency_ms=job.latency_ms,
                    source=job.source,
                )
                if self.live_feed is not None:
                    # Après process_cycle (jamais avant) : round_results et match_feed référencent
                    # events, upserté juste au-dessus dans la même transaction connexion.
                    try:
                        await self.live_feed.process_games(
                            conn, job.response.games,
                            league_id=job.league_id, league_name=self.leagues[job.league_id],
                        )
                    except BlockedError as exc:
                        # Le résultat de ce cycle reste valable (déjà écrit) ; seuls les cycles
                        # suivants sont concernés par l'arrêt.
                        await self._stop_if_blocked(exc, context=f"fil de match, ligue {job.league_id}")
            metrics.database_write_seconds.observe(time.monotonic() - t0)
            league_label = str(job.league_id)
            metrics.events_collected_total.labels(league=league_label).inc(result.n_games)
            metrics.odds_rows_written_total.labels(league=league_label).inc(result.n_rows_written)
            metrics.last_cycle_timestamp_seconds.labels(league=league_label).set_to_current_time()
            await self._log_collection(
                league_id=job.league_id, source=job.source,
                endpoint="gamesByChamp" if job.source == SOURCE_V3 else "champzip+gamezip",
                ok=True, http_status=200, latency_ms=job.latency_ms,
                n_games=result.n_games, n_rows_written=result.n_rows_written,
            )
            self.metrics.cycles += 1
            self.metrics.rows_written += result.n_rows_written
            return result
        finally:
            self.queue.task_done()
            metrics.queue_depth.set(self.queue.qsize())

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
            metrics.blocked_total.labels(source="cdn").inc()
            await self._send_alert(
                "dictionary_blocked", "⚠️ Dictionnaire des marchés inaccessible",
                f"Le CDN des libellés de marchés est bloqué ou indisponible : {exc}\n"
                "La collecte des cotes continue normalement ; seuls les nouveaux marchés resteront "
                "sans libellé lisible tant que ce n'est pas résolu.",
            )
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
                await self._stop_if_blocked(exc, context=f"réconciliation des résultats, ligue {league_id}")
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
                await self._stop_if_blocked(exc, context=f"rattrapage des résultats, ligue {league_id}")
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
