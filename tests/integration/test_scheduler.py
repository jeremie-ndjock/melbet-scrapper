"""Tests de l'ordonnanceur : concurrence entre ligues, file bornée, reprise sans doublon,
arrêt total sur blocage. Aucun réseau réel (httpx.MockTransport)."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from collector.scheduler import Scheduler
from collector.transport.http import HttpClient, RetryConfig
from collector.transport.ratelimit import RateLimiter

MK_X, MK_3 = 1252965, 2282406
LEAGUES = {MK_X: "Mortal Kombat X", MK_3: "Mortal Kombat 3"}
SITE_PARAMS = {"lng": "fr", "ref": 8}


def _game(game_id: int, cf: float) -> dict:
    return {
        "id": game_id, "startTs": 1000, "updateTs": 1000 + int(cf * 1000),
        "opponent1": {"fullName": "A", "opps": [{"id": 1}]},
        "opponent2": {"fullName": "B", "opps": [{"id": 2}]},
        "scores": {"fullScore": "0-0", "currentPeriodName": "1er round", "fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0}},
        # La deuxième sélection n'a pas besoin d'être réaliste (V1 + V2 ne somment pas à 1 ici),
        # seulement de rester une cote valide (positive) quelle que soit la valeur de `cf` testée.
        "eventGroups": [{"groupId": 1, "events": [[{"type": 1, "cf": cf}], [{"type": 3, "cf": round(10 / cf, 3)}]]}],
    }


class FakeMelbet:
    """Simule les endpoints du site : gamesByChamp (un match par ligue, cote pilotable par le
    test) et le service de résultats (vide par défaut)."""

    def __init__(self):
        self.cf = {MK_X: 1.5, MK_3: 2.0}
        self.games_calls: list[int] = []
        self.blocked = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        if self.blocked:
            return httpx.Response(403, text="bloqué")
        if "gamesByChamp" in request.url.path:
            champ_id = int(q["champId"][0])
            self.games_calls.append(champ_id)
            body = {"liga": {"id": champ_id, "name": "x"}, "gamesCount": 1,
                    "games": [_game(champ_id * 10, self.cf[champ_id])]}
            return httpx.Response(200, text=json.dumps(body))
        if "result/web/api/v3/games" in request.url.path:
            return httpx.Response(200, text=json.dumps({"count": 0, "items": []}))
        return httpx.Response(404)


class FakeCdn:
    """CDN minimal : une table vide (aucun chunk référencé), suffisant pour les tests où seule
    l'absence d'erreur du rafraîchissement du dictionnaire compte."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({}))


def make_http(handler, *, retry: RetryConfig | None = None) -> HttpClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://melbet.test")
    return HttpClient("https://melbet.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000),
                       client=inner, retry=retry or RetryConfig(max_attempts=1))


def make_scheduler(db_pool, melbet: FakeMelbet, cdn: FakeCdn, *, leagues=None, queue_maxsize=100) -> Scheduler:
    return Scheduler(
        http=make_http(melbet.handler), cdn_http=make_http(cdn.handler), db_pool=db_pool,
        leagues=leagues or LEAGUES, site_params=SITE_PARAMS, poll_interval=5.0, queue_maxsize=queue_maxsize,
    )


async def test_two_leagues_are_polled_and_written_independently(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn())

    for league_id in LEAGUES:
        assert await sched.poll_once(league_id) is True
    for _ in LEAGUES:
        result = await sched.write_once()
        assert result is not None and result.n_games == 1

    async with db_pool.acquire() as conn:
        leagues_in_db = {r["league_id"] for r in await conn.fetch("SELECT DISTINCT league_id FROM events")}
    assert leagues_in_db == set(LEAGUES)
    assert set(melbet.games_calls) == set(LEAGUES)  # chaque ligue a bien été interrogée


async def test_queue_is_bounded_and_applies_backpressure(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, queue_maxsize=1)

    assert await sched.poll_once(MK_X) is True  # remplit la file (capacité 1)

    blocked_put = asyncio.ensure_future(sched.poll_once(MK_X))
    await asyncio.sleep(0.05)
    assert not blocked_put.done()  # la file est pleine : le producteur est mis en attente

    await sched.write_once()  # libère une place
    await asyncio.wait_for(blocked_put, timeout=2.0)  # le second `poll_once` peut enfin aboutir
    await sched.write_once()


async def test_restart_after_preload_does_not_rewrite_known_state(db_pool):
    melbet = FakeMelbet()
    first = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})
    await first.poll_once(MK_X)
    await first.write_once()
    async with db_pool.acquire() as conn:
        n_after_first = await conn.fetchval("SELECT count(*) FROM odds_snapshots")
    assert n_after_first > 0

    # Un nouveau processus (nouveaux détecteurs, vides) : sans préchargement, il réécrirait tout.
    second = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})
    await second.preload_all()
    await second.poll_once(MK_X)  # même cote qu'avant (`melbet.cf` inchangé) : rien de nouveau
    await second.write_once()

    async with db_pool.acquire() as conn:
        n_after_restart = await conn.fetchval("SELECT count(*) FROM odds_snapshots")
    assert n_after_restart == n_after_first  # la reprise n'a rien réécrit inutilement

    # Une vraie évolution, elle, est toujours détectée après la reprise.
    melbet.cf[MK_X] = 9.99
    await second.poll_once(MK_X)
    await second.write_once()
    async with db_pool.acquire() as conn:
        n_after_change = await conn.fetchval("SELECT count(*) FROM odds_snapshots")
    assert n_after_change > n_after_restart


async def test_blocked_response_stops_the_scheduler_without_raising(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})
    melbet.blocked = True

    assert await sched.poll_once(MK_X) is False  # signale l'arrêt à l'appelant
    assert sched.stopping is True
    assert sched.metrics.blocked is True


async def test_full_run_stops_cleanly_when_blocked_from_the_start(db_pool):
    """`run()` orchestre tout (préchargement, rattrapage, boucles) ; si le site est bloqué dès le
    début, l'ensemble doit s'arrêter proprement, sans tâche qui reste bloquée indéfiniment."""
    melbet = FakeMelbet()
    melbet.blocked = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    await asyncio.wait_for(
        sched.run(dictionary_refresh_interval=999, reconcile_interval=999, backfill_days=1),
        timeout=5.0,
    )
    assert sched.stopping is True
    assert sched.metrics.blocked is True


async def test_run_stops_promptly_on_explicit_stop_request(db_pool):
    """En fonctionnement normal (pas de blocage), `stop()` doit arrêter `run()` en un temps borné,
    condition nécessaire pour un arrêt propre sur SIGTERM (voir main.py)."""
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    async def stop_soon():
        # Laisse largement le temps au démarrage (préchargement, rattrapage initial) de se
        # terminer sur une machine lente, avant de couper : on veut observer un arrêt propre
        # une fois la collecte réellement en cours, pas interrompre le démarrage lui-même.
        await asyncio.sleep(2.0)
        sched.stop()

    asyncio.ensure_future(stop_soon())
    await asyncio.wait_for(
        sched.run(dictionary_refresh_interval=999, reconcile_interval=999, backfill_days=1),
        timeout=15.0,
    )
    assert sched.metrics.cycles >= 1  # au moins un cycle a bien eu lieu avant l'arrêt


async def test_dictionary_and_results_wrappers_reach_all_leagues(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn())

    n = await sched.refresh_dictionary_once()
    assert n == 0  # rien à résoudre : aucune cote encore stockée

    n = await sched.reconcile_results_once()
    assert n == 0  # le faux service de résultats ne renvoie rien, mais les deux ligues sont bien interrogées

    n = await sched.backfill_once(days_back=1)
    assert n == 0
    async with db_pool.acquire() as conn:
        checkpoints = {r["key"] for r in await conn.fetch("SELECT key FROM checkpoints WHERE worker = 'results'")}
    assert checkpoints == {f"backfill_{lid}" for lid in LEAGUES}  # un point de contrôle par ligue
