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


def _champ_zip_body(champ_id: int, game_id: int, cf: float) -> dict:
    return {"Success": True, "Value": {"G": [{
        "I": game_id, "O1": "A", "O2": "B", "O1I": 1, "O2I": 2, "S": 1000,
        "SC": {"FS": {"S1": 0, "S2": 0}, "CP": 1, "CPS": "1er round", "TS": 0},
    }]}}


def _game_zip_body(game_id: int, cf: float) -> dict:
    return {"Success": True, "Value": {
        "I": game_id, "U": 1000 + int(cf * 1000), "S": 1000, "O1": "A", "O2": "B", "O1I": 1, "O2I": 2,
        "SC": {"FS": {"S1": 0, "S2": 0}, "CP": 1, "CPS": "1er round", "TS": 0},
        "GE": [{"G": 1, "E": [[{"T": 1, "C": cf}], [{"T": 3, "C": round(10 / cf, 3)}]]}],
    }}


class FakeMelbet:
    """Simule les endpoints du site : gamesByChamp et la source de secours legacy (un match par
    ligue, cote pilotable par le test), et le service de résultats (vide par défaut)."""

    def __init__(self):
        self.cf = {MK_X: 1.5, MK_3: 2.0}
        self.games_calls: list[int] = []
        self.legacy_calls: list[str] = []
        self.blocked = False
        # Simule un changement de structure du site : gamesByChamp renvoie un JSON qui ne valide
        # plus le schéma attendu (comme si un champ requis avait disparu ou changé de forme).
        self.v3_schema_broken = False
        # Simule l'absence de match en ce moment pour une ligue (204 No Content), un état normal
        # rencontré avec AI Table Tennis (Memoire.md, section 32) — pas une erreur de schéma.
        self.no_games_204: set[int] = set()
        # Score courant par ligue (0-0 par défaut) et tableau des rounds pour /v3/statistic,
        # utilisés par les tests du fil de match en direct (voir test_live_feed_*).
        self.score: dict[int, tuple[int, int]] = {}
        self.round_table: dict[int, list[dict]] = {}
        self.period_name: dict[int, str] = {}
        self.statistic_calls: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        if self.blocked:
            return httpx.Response(403, text="bloqué")
        if "GetChampZip" in request.url.path:
            self.legacy_calls.append("champzip")
            champ_id = int(q["champ"][0])
            return httpx.Response(200, text=json.dumps(_champ_zip_body(champ_id, champ_id * 10, self.cf[champ_id])))
        if "GetGameZip" in request.url.path:
            self.legacy_calls.append("gamezip")
            game_id = int(q["id"][0])
            champ_id = game_id // 10
            return httpx.Response(200, text=json.dumps(_game_zip_body(game_id, self.cf[champ_id])))
        if "v3/statistic" in request.url.path:
            game_id = int(q["gameId"][0])
            self.statistic_calls.append(game_id)
            body = {"fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0},
                    "statistic": {"main": {"RoundTable": json.dumps(self.round_table.get(game_id, []))}}}
            return httpx.Response(200, text=json.dumps(body))
        if "gamesByChamp" in request.url.path:
            champ_id = int(q["champId"][0])
            self.games_calls.append(champ_id)
            if champ_id in self.no_games_204:
                return httpx.Response(204)
            if self.v3_schema_broken:
                # Un champ requis (`id`) a disparu : la validation de schéma doit échouer.
                body = {"liga": {"id": champ_id, "name": "x"}, "gamesCount": 1,
                        "games": [{k: v for k, v in _game(champ_id * 10, self.cf[champ_id]).items() if k != "id"}]}
            else:
                game = _game(champ_id * 10, self.cf[champ_id])
                s1, s2 = self.score.get(champ_id, (0, 0))
                game["scores"]["fullScoreDetail"] = {"scoreOpp1": s1, "scoreOpp2": s2}
                game["scores"]["fullScore"] = f"{s1}-{s2}"
                if champ_id in self.period_name:
                    game["scores"]["currentPeriodName"] = self.period_name[champ_id]
                body = {"liga": {"id": champ_id, "name": "x"}, "gamesCount": 1, "games": [game]}
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


class FakeAlerter:
    """Remplace ``ThrottledAlerter`` dans les tests : enregistre les alertes sans réseau réel."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    async def alert(self, key: str, subject: str, message: str) -> bool:
        self.calls.append((key, subject, message))
        return True

    def reset(self, key: str) -> None:
        pass


def make_scheduler(db_pool, melbet: FakeMelbet, cdn: FakeCdn, *, leagues=None, queue_maxsize=100,
                    recovery_probe_cycles=12, alerter=None, live_feed=None) -> Scheduler:
    leagues = leagues or LEAGUES
    return Scheduler(
        http=make_http(melbet.handler), cdn_http=make_http(cdn.handler), db_pool=db_pool,
        leagues=leagues, league_sport_ids={league_id: 103 for league_id in leagues},
        site_params=SITE_PARAMS, legacy_site_params=SITE_PARAMS,
        poll_interval=5.0, queue_maxsize=queue_maxsize, recovery_probe_cycles=recovery_probe_cycles,
        alerter=alerter, live_feed=live_feed,
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


async def test_schema_change_falls_back_to_legacy_and_records_dead_letter(db_pool):
    """Simule un changement de structure du site (un champ requis disparaît de la réponse) :
    le cycle ne doit pas être perdu, la réponse brute doit être archivée, et les cycles suivants
    doivent continuer sur la source de secours sans redemander le v3 à chaque fois."""
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, recovery_probe_cycles=100)

    assert await sched.poll_once(MK_X) is True  # le cycle aboutit malgré tout, via la source de secours
    assert MK_X in sched._degraded
    assert sched.metrics.schema_changes_detected == 1
    assert melbet.legacy_calls == ["champzip", "gamezip"]  # la source de secours a bien été utilisée

    result = await sched.write_once()
    assert result is not None and result.n_games == 1  # les données de secours sont bien exploitables

    async with db_pool.acquire() as conn:
        dl = await conn.fetchrow("SELECT source, endpoint, league_id FROM dead_letter")
    assert dl is not None and dl["source"] == 1 and dl["league_id"] == MK_X

    # Le cycle suivant reste sur la source de secours sans retenter le v3 (probe lointaine).
    calls_before = len(melbet.games_calls)
    assert await sched.poll_once(MK_X) is True
    assert len(melbet.games_calls) == calls_before  # pas de nouvel appel v3
    assert len(melbet.legacy_calls) == 4  # un nouveau cycle complet côté secours


async def test_recovers_from_degraded_state_once_the_schema_is_fixed(db_pool):
    """Une fois la source principale rétablie, l'ordonnanceur doit y revenir de lui-même au
    prochain essai périodique, sans intervention."""
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, recovery_probe_cycles=2)

    await sched.poll_once(MK_X)          # cycle 1 : bascule (probe dans 2 cycles)
    await sched.write_once()
    await sched.poll_once(MK_X)          # cycle 2 : toujours dégradé (probe dans 1 cycle)
    await sched.write_once()
    assert MK_X in sched._degraded

    melbet.v3_schema_broken = False      # le site est "réparé"
    v3_calls_before = len(melbet.games_calls)
    assert await sched.poll_once(MK_X) is True  # cycle 3 : nouvel essai du v3, qui réussit
    await sched.write_once()

    assert MK_X not in sched._degraded
    assert len(melbet.games_calls) == v3_calls_before + 1


async def test_no_games_currently_204_does_not_trigger_a_fallback(db_pool):
    """Régression du vrai défaut de production trouvé le 2026-09-23 (Memoire.md, section 32) :
    un 204 No Content (aucun match en ce moment, arrivé avec AI Table Tennis) déclenchait à tort
    une bascule sur la source de secours — jamais rencontré avec Mortal Kombat auparavant."""
    melbet = FakeMelbet()
    melbet.no_games_204.add(MK_X)
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    assert await sched.poll_once(MK_X) is True
    assert MK_X not in sched._degraded
    assert sched.metrics.schema_changes_detected == 0
    assert melbet.legacy_calls == []  # jamais appelée : pas une panne, rien à contourner

    result = await sched.write_once()
    assert result is not None and result.n_games == 0


async def test_both_sources_failing_is_reported_but_does_not_stop_the_scheduler(db_pool):
    """Si la source de secours échoue elle aussi (panne transitoire), le cycle est perdu proprement
    (compté en erreur) sans arrêter l'ordonnanceur — contrairement à un blocage explicite."""
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    # Casse aussi la source de secours (endpoint legacy renvoyant une 404, donc une ServerError).
    def broken_secondary(request: httpx.Request) -> httpx.Response:
        if "GetChampZip" in request.url.path or "GetGameZip" in request.url.path:
            return httpx.Response(500)
        return melbet.handler(request)

    sched.http = make_http(broken_secondary, retry=RetryConfig(max_attempts=1, base_delay_seconds=0.01, max_delay_seconds=0.01))

    assert await sched.poll_once(MK_X) is True  # pas un blocage : on continue au cycle suivant
    assert sched.stopping is False
    assert sched.metrics.errors >= 1


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


# ---------------------------------------------------------------- alertes et journal de collecte

async def test_blocked_sends_an_alert(db_pool):
    melbet = FakeMelbet()
    alerter = FakeAlerter()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, alerter=alerter)
    melbet.blocked = True

    await sched.poll_once(MK_X)

    assert len(alerter.calls) == 1
    key, subject, message = alerter.calls[0]
    assert key == "blocked" and "bloqué" in subject.lower()


async def test_schema_change_sends_exactly_one_alert_across_several_degraded_cycles(db_pool):
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    alerter = FakeAlerter()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, recovery_probe_cycles=100, alerter=alerter)

    await sched.poll_once(MK_X)
    await sched.write_once()
    await sched.poll_once(MK_X)  # toujours dégradé : ne doit pas ré-alerter
    await sched.write_once()

    degraded_alerts = [c for c in alerter.calls if c[0].startswith("degraded:")]
    assert len(degraded_alerts) == 1


async def test_recovery_sends_an_alert_and_resets_the_throttle(db_pool):
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    alerter = FakeAlerter()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, recovery_probe_cycles=1, alerter=alerter)

    await sched.poll_once(MK_X)  # dégradation
    await sched.write_once()
    melbet.v3_schema_broken = False
    await sched.poll_once(MK_X)  # rétablissement (probe au cycle suivant)
    await sched.write_once()

    kinds = [c[0].split(":")[0] for c in alerter.calls]
    assert kinds == ["degraded", "recovered"]


async def test_dictionary_blocked_sends_an_alert_without_stopping_the_scheduler(db_pool):
    melbet = FakeMelbet()
    cdn = FakeCdn()
    alerter = FakeAlerter()
    sched = make_scheduler(db_pool, melbet, cdn, leagues={MK_X: "x"}, alerter=alerter)

    # Il faut d'abord des cotes non résolues en base, sans quoi refresh_dictionary_once n'a rien à
    # chercher et ne touche jamais le CDN (voir test_dictionary_and_results_wrappers_reach_all_leagues).
    await sched.poll_once(MK_X)
    await sched.write_once()

    def blocked_cdn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)
    sched.cdn_http = make_http(blocked_cdn)

    n = await sched.refresh_dictionary_once()
    assert n == 0
    assert sched.metrics.dictionary_blocked is True
    assert sched.stopping is False  # un blocage du CDN n'arrête pas la collecte des cotes
    assert any(c[0] == "dictionary_blocked" for c in alerter.calls)


async def test_successful_cycle_is_recorded_in_collection_log_with_correct_source(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    await sched.poll_once(MK_X)
    await sched.write_once()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT source, ok, n_games, n_rows_written FROM collection_log WHERE league_id = $1", MK_X)
    assert row["source"] == 1 and row["ok"] is True and row["n_games"] == 1 and row["n_rows_written"] > 0


async def test_legacy_fallback_cycle_is_recorded_with_legacy_source_not_v3(db_pool):
    """Un vrai défaut trouvé pendant l'étape 8 : les cotes de la source de secours étaient
    marquées comme venant de la source principale. Ce test verrouille la correction."""
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    await sched.poll_once(MK_X)  # bascule sur la source de secours dès ce cycle
    await sched.write_once()

    async with db_pool.acquire() as conn:
        odds_source = await conn.fetchval("SELECT DISTINCT source FROM odds_snapshots")
        log_source = await conn.fetchval("SELECT source FROM collection_log WHERE league_id = $1", MK_X)
    assert odds_source == 2 and log_source == 2  # 2 = secours, jamais 1 (source principale)


# ---------------------------------------------------------------- étape 9 : comblement des manques

async def test_write_once_returns_none_when_the_queue_is_empty(db_pool):
    sched = make_scheduler(db_pool, FakeMelbet(), FakeCdn(), leagues={MK_X: "x"})
    result = await sched.write_once(timeout=0.05)
    assert result is None


async def test_preload_all_stops_immediately_on_a_blocked_response(db_pool):
    melbet = FakeMelbet()
    melbet.blocked = True
    sched = make_scheduler(db_pool, melbet, FakeCdn())  # les deux ligues

    await sched.preload_all()

    assert sched.stopping is True
    assert sched.metrics.blocked is True


async def test_preload_all_skips_a_league_with_a_transient_error_and_continues(db_pool, caplog):
    """Une erreur transitoire (5xx) au préchargement d'une ligue ne doit pas empêcher le
    préchargement des autres : cette ligue traitera simplement son prochain cycle comme un tout
    premier lancement (voir la docstring de `preload_all`)."""
    melbet = FakeMelbet()

    def one_league_down(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        if "gamesByChamp" in request.url.path and q.get("champId", [""])[0] == str(MK_X):
            return httpx.Response(500)
        return melbet.handler(request)

    sched = make_scheduler(db_pool, melbet, FakeCdn())
    sched.http = make_http(one_league_down, retry=RetryConfig(max_attempts=1, base_delay_seconds=0.01, max_delay_seconds=0.01))

    with caplog.at_level("WARNING"):
        await sched.preload_all()  # ne lève rien

    assert sched.metrics.blocked is False
    assert any("préchargement impossible" in r.message for r in caplog.records)
    # La ligue saine, elle, a bien été préchargée sans encombre (aucune exception propagée jusqu'ici).


async def test_backfill_stops_immediately_if_already_stopping(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn())  # les deux ligues
    sched.stop()  # simule un arrêt demandé (SIGTERM) juste avant le rattrapage

    total = await sched.backfill_once(days_back=1)

    assert total == 0
    assert melbet.games_calls == []  # aucune ligue n'a été interrogée


async def test_reconcile_stops_immediately_if_already_stopping(db_pool):
    melbet = FakeMelbet()
    sched = make_scheduler(db_pool, melbet, FakeCdn())
    sched.stop()

    total = await sched.reconcile_results_once()

    assert total == 0


async def test_periodic_task_survives_a_transient_exception_and_retries(db_pool):
    """Une tâche périodique (rafraîchissement du dictionnaire, réconciliation…) qui échoue une fois
    ne doit pas rester bloquée : `_periodic` journalise et retente au prochain intervalle."""
    sched = make_scheduler(db_pool, FakeMelbet(), FakeCdn(), leagues={MK_X: "x"})
    calls: list[int] = []

    async def action():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("panne transitoire simulée")
        sched.stop()

    await asyncio.wait_for(sched._periodic(action, interval_seconds=0.01, name="test"), timeout=2.0)

    assert calls == [1, 2]  # a survécu à l'échec du premier appel et a bien retenté


async def test_degraded_league_reports_failure_without_stopping_when_not_yet_probing_and_legacy_fails(db_pool):
    """Une fois dégradée, une ligue interroge directement la source de secours (sans retenter le v3
    avant le cycle de sondage prévu). Si cette source de secours échoue à son tour à ce moment-là,
    le cycle est perdu proprement, sans arrêter l'ordonnanceur."""
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, recovery_probe_cycles=100)

    assert await sched.poll_once(MK_X) is True  # cycle 1 : bascule (ParserError), écrit via la source de secours
    await sched.write_once()
    assert MK_X in sched._degraded

    def broken_secondary(request: httpx.Request) -> httpx.Response:
        if "GetChampZip" in request.url.path or "GetGameZip" in request.url.path:
            return httpx.Response(500)
        return melbet.handler(request)
    sched.http = make_http(broken_secondary, retry=RetryConfig(max_attempts=1, base_delay_seconds=0.01, max_delay_seconds=0.01))

    result = await sched.poll_once(MK_X)  # cycle 2 : toujours dégradée, pas encore de nouvel essai du v3

    assert result is True          # pas un blocage : le cycle suivant pourra réessayer
    assert sched.stopping is False
    assert sched.metrics.errors >= 1


async def test_failed_cycle_is_recorded_in_collection_log_as_not_ok(db_pool):
    melbet = FakeMelbet()
    melbet.v3_schema_broken = True  # source principale cassée...
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"})

    # ... et la source de secours aussi, pour forcer un cycle entièrement en échec.
    def broken_secondary(request: httpx.Request) -> httpx.Response:
        if "GetChampZip" in request.url.path or "GetGameZip" in request.url.path:
            return httpx.Response(500)
        return melbet.handler(request)
    sched.http = make_http(broken_secondary, retry=RetryConfig(max_attempts=1, base_delay_seconds=0.01, max_delay_seconds=0.01))

    await sched.poll_once(MK_X)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT ok, error FROM collection_log WHERE league_id = $1", MK_X)
    assert row["ok"] is False and row["error"] is not None


# ---------------------------------------------------------------- fil de match en direct (2026-09-22)

class RecordingSender:
    """Remplace ``MatchFeedSender`` : enregistre les envois/éditions sans réseau réel."""

    def __init__(self):
        self.sent: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self.chat_id = "-1"
        self._next_id = 5000

    async def send_or_edit(self, chat_id, message_id, text):
        if message_id is not None:
            self.edited.append((message_id, text))
            return message_id
        self._next_id += 1
        self.sent.append(text)
        return self._next_id


async def test_live_feed_sends_a_telegram_update_after_a_completed_round(db_pool):
    from collector.live_feed import LiveFeedProcessor

    melbet = FakeMelbet()
    melbet.score[MK_X] = (1, 0)
    melbet.round_table[MK_X * 10] = [{"R": 1, "T": 31, "W": "A", "DI": "Regular", "WT": "0", "FW": False}]
    sender = RecordingSender()
    live_feed = LiveFeedProcessor(http=make_http(melbet.handler), site_params=SITE_PARAMS, sender=sender, chat_ids={MK_X: "-1"})
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, live_feed=live_feed)

    await sched.poll_once(MK_X)
    await sched.write_once()

    assert len(sender.sent) == 1
    assert "💥 Manche 1 : Vainqueur A" in sender.sent[0]
    assert melbet.statistic_calls == [MK_X * 10]


async def test_live_feed_does_not_call_statistic_when_the_score_has_not_changed(db_pool):
    from collector.live_feed import LiveFeedProcessor

    melbet = FakeMelbet()  # score 0-0 par défaut : aucune manche terminée
    sender = RecordingSender()
    live_feed = LiveFeedProcessor(http=make_http(melbet.handler), site_params=SITE_PARAMS, sender=sender, chat_ids={MK_X: "-1"})
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, live_feed=live_feed)

    await sched.poll_once(MK_X)
    await sched.write_once()

    assert sender.sent == []
    assert melbet.statistic_calls == []


async def test_live_feed_blocked_stops_the_scheduler_but_keeps_the_cycle_result(db_pool):
    """Un blocage détecté par le fil de match doit arrêter l'ordonnanceur (même politique que
    partout ailleurs), sans pour autant perdre les cotes déjà écrites par ce même cycle."""
    from collector.live_feed import LiveFeedProcessor

    melbet = FakeMelbet()
    melbet.score[MK_X] = (1, 0)
    sender = RecordingSender()
    live_feed = LiveFeedProcessor(http=make_http(melbet.handler), site_params=SITE_PARAMS, sender=sender, chat_ids={MK_X: "-1"})
    sched = make_scheduler(db_pool, melbet, FakeCdn(), leagues={MK_X: "x"}, live_feed=live_feed)

    await sched.poll_once(MK_X)
    melbet.blocked = True  # le blocage survient au moment de l'appel /v3/statistic dans write_once

    result = await sched.write_once()

    assert result is not None and result.n_games == 1  # le cycle a bien été écrit
    assert sched.stopping is True
    assert sched.metrics.blocked is True
