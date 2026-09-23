"""Tests d'intégration : stockage des résultats et rattrapage de l'historique avec reprise."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from collector.results import (RawResultGame, align_down, backfill_results, iter_windows,
                                parse_table_tennis_score, store_results)
from collector.transport.http import HttpClient, RetryConfig
from collector.transport.ratelimit import RateLimiter

MK_X = 1252965
AI_TT = 3066896
SITE_PARAMS = {"lng": "fr", "ref": 8}


def game(id, score, opp1="A", opp2="B", date_start=1000):
    return RawResultGame(id=id, champId=MK_X, opp1=opp1, opp2=opp2, opp1Ids=[10], opp2Ids=[20],
                          score=score, dateStart=date_start)


async def test_store_results_is_idempotent(db):
    games = [game(1, "5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)")]
    n1 = await store_results(db, MK_X, games)
    n2 = await store_results(db, MK_X, games)
    assert n1 == 1 and n2 == 1  # store_results retourne le nombre de résultats traités les deux fois
    assert await db.fetchval("SELECT count(*) FROM results") == 1  # mais rien n'est dupliqué en base
    assert await db.fetchval("SELECT count(*) FROM round_results WHERE game_id = 1") == 5


async def test_store_results_completes_round_without_overwriting_live_data(db):
    # Comme le ferait le tableau des rounds en direct (étape 4) : durée et libellé déjà connus.
    from collector.storage import queries
    await db.execute(queries.UPSERT_ROUND_RESULT, 2, 1, 1, 42, "Fatality", None, "0", True, None, None)

    await store_results(db, MK_X, [game(2, "5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)")])

    row = await db.fetchrow("SELECT * FROM round_results WHERE game_id = 2 AND round_no = 1")
    assert row["seconds"] == 42 and row["finish_di"] == "Fatality"  # non écrasés
    assert row["finish_code"] == "F"  # complété par le résultat officiel


async def test_store_results_skips_unparsable_score_without_failing_the_batch(db):
    n = await store_results(db, MK_X, [game(3, "score invalide"), game(4, "5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)")])
    assert n == 1  # le résultat 3 est ignoré, le résultat 4 est bien stocké
    assert await db.fetchval("SELECT count(*) FROM results WHERE game_id = 3") == 0
    assert await db.fetchval("SELECT count(*) FROM results WHERE game_id = 4") == 1


async def test_store_results_with_table_tennis_parser_skips_round_results(db):
    """AI Table Tennis n'a pas de notion de finish-type : ``round_results`` (spécifique Mortal
    Kombat) ne doit pas être renseignée, seul ``results`` doit l'être (Memoire.md, section 27)."""
    tt_game = RawResultGame(id=5, champId=AI_TT, opp1="A", opp2="B", opp1Ids=[10], opp2Ids=[20],
                             score="2:0 (11:7,11:7)", dateStart=1000)
    n = await store_results(db, AI_TT, [tt_game], parse_fn=parse_table_tennis_score)
    assert n == 1
    assert await db.fetchval("SELECT count(*) FROM results WHERE game_id = 5") == 1
    assert await db.fetchval("SELECT count(*) FROM round_results WHERE game_id = 5") == 0


def _mock_client(handler, *, retry: RetryConfig | None = None):
    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(transport=transport, base_url="https://melbet.test")
    return HttpClient("https://melbet.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000),
                       client=inner, retry=retry or RetryConfig(max_attempts=1))


def _window_body(games: list[dict]) -> str:
    return json.dumps({"count": len(games), "items": games})


async def test_backfill_queries_expected_number_of_windows_and_stores_results(db):
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    requested_windows: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        date_from, date_to = int(q["dateFrom"][0]), int(q["dateTo"][0])
        requested_windows.append((date_from, date_to))
        game_id = 100000 + len(requested_windows)
        return httpx.Response(200, text=_window_body([
            {"id": game_id, "champId": MK_X, "opp1": "A", "opp2": "B", "opp1Ids": [1], "opp2Ids": [2],
             "score": "5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)", "dateStart": date_from}
        ]))

    client = _mock_client(handler)
    n_stored = await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=5, now=now)

    expected_windows = iter_windows(end=now, days_back=5)
    assert len(requested_windows) == len(expected_windows)
    assert n_stored == len(expected_windows)
    assert await db.fetchval("SELECT count(*) FROM results") == len(expected_windows)
    for date_from, date_to in requested_windows:  # les requêtes respectent les contraintes du service
        assert date_from % 300 == 0 and date_to % 300 == 0
        assert date_to - date_from <= 2 * 86400


async def test_backfill_resumes_without_requerying_done_windows(db):
    """Usage réaliste : redémarrage avec la même profondeur de configuration (``days_back``
    constant, voir la mise en garde dans results.py). La grille reste ancrée sur le premier
    « now », pas sur celui de la reprise, et aucune fenêtre n'est requêtée deux fois."""
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    all_requested: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        date_from, date_to = int(q["dateFrom"][0]), int(q["dateTo"][0])
        all_requested.append((date_from, date_to))
        return httpx.Response(200, text=_window_body([]))

    client = _mock_client(handler)

    await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=7, now=now)
    n_after_first = len(all_requested)
    assert n_after_first == len(iter_windows(end=now, days_back=7)) > 1

    # Reprise (horloge murale différente, ex. redémarrage du conteneur le lendemain), même profondeur.
    await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=7, now=now + timedelta(hours=30))

    assert len(all_requested) == n_after_first  # tout était déjà couvert : aucune requête de plus
    as_datetimes = {(datetime.fromtimestamp(a, tz=timezone.utc), datetime.fromtimestamp(b, tz=timezone.utc))
                    for a, b in all_requested}
    assert as_datetimes == set(iter_windows(end=now, days_back=7))  # grille ancrée sur le premier `now`


async def test_reconcile_recent_covers_a_short_lookback_window_and_is_safe_to_repeat(db):
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        calls.append((int(q["dateFrom"][0]), int(q["dateTo"][0])))
        return httpx.Response(200, text=_window_body([
            {"id": 42, "champId": MK_X, "opp1": "A", "opp2": "B",
             "score": "5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)", "dateStart": int(now.timestamp())}
        ]))

    client = _mock_client(handler)
    from collector.results import reconcile_recent

    n1 = await reconcile_recent(client, db, MK_X, SITE_PARAMS, sport_id=103, lookback_hours=2, now=now)
    n2 = await reconcile_recent(client, db, MK_X, SITE_PARAMS, sport_id=103, lookback_hours=2, now=now)  # appelé de nouveau, sans dégât
    assert n1 == 1 and n2 == 1
    assert await db.fetchval("SELECT count(*) FROM results") == 1  # store_results reste idempotent
    date_from, date_to = calls[0]
    # `date_to` est arrondi au multiple de 300 s SUPÉRIEUR (borne haute exclusive, comme
    # `iter_windows`) : la fenêtre peut donc dépasser les 2 h demandées de quelques minutes au plus.
    assert 2 * 3600 <= date_to - date_from <= 2 * 3600 + 300
    assert date_from % 300 == 0 and date_to % 300 == 0


async def test_backfill_resumes_after_a_mid_run_crash(db):
    """Simule une vraie interruption (panne réseau, redémarrage forcé) en plein rattrapage : la
    progression déjà faite (chaque fenêtre met à jour le point de contrôle immédiatement après son
    traitement) doit survivre, et la reprise ne refait que ce qui restait."""
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    requested: list[tuple[int, int]] = []
    fail_after = 2

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        requested.append((int(q["dateFrom"][0]), int(q["dateTo"][0])))
        if len(requested) > fail_after:
            raise httpx.ConnectError("coupure simulée")
        return httpx.Response(200, text=_window_body([]))

    client = _mock_client(flaky_handler)
    with pytest.raises(Exception):
        await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=7, now=now)
    n_before_crash = len(requested)
    assert 0 < n_before_crash < len(iter_windows(end=now, days_back=7))  # interrompu en cours de route

    # Reprise avec un client qui fonctionne : seules les fenêtres restantes sont demandées.
    def reliable_handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        requested.append((int(q["dateFrom"][0]), int(q["dateTo"][0])))
        return httpx.Response(200, text=_window_body([]))

    client2 = _mock_client(reliable_handler)
    await backfill_results(client2, db, MK_X, SITE_PARAMS, sport_id=103, days_back=7, now=now)

    total_windows = len(iter_windows(end=now, days_back=7))
    # La fenêtre qui a échoué juste avant le crash est légitimement redemandée après la reprise
    # (elle n'avait jamais réussi) : c'est le seul doublon attendu, pas un gaspillage supplémentaire.
    assert len(requested) == total_windows + 1
    # Les fenêtres qui avaient réussi avant le crash, elles, ne sont jamais redemandées.
    succeeded_before_crash = requested[:fail_after]
    assert all(w not in requested[n_before_crash:] for w in succeeded_before_crash)


async def test_backfill_with_no_new_range_makes_no_request(db):
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=_window_body([]))

    client = _mock_client(handler)
    await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=3, now=now)
    n_first = calls["n"]
    await backfill_results(client, db, MK_X, SITE_PARAMS, sport_id=103, days_back=3, now=now)  # rien de nouveau à couvrir
    assert calls["n"] == n_first  # aucun appel supplémentaire


async def test_backfill_sends_the_given_sport_id_and_uses_the_given_parser(db):
    """Régression pour le bug réel trouvé en généralisant results.py : ``sportIds`` était figé à
    103 (Mortal Kombat) quel que soit le sport demandé (Memoire.md, section 27)."""
    now = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    sent_sport_ids: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        sent_sport_ids.append(int(q["sportIds"][0]))
        return httpx.Response(200, text=_window_body([
            {"id": 999, "champId": AI_TT, "opp1": "A", "opp2": "B", "opp1Ids": [1], "opp2Ids": [2],
             "score": "2:0 (11:7,11:7)", "dateStart": int(now.timestamp())}
        ]))

    client = _mock_client(handler)
    n_stored = await backfill_results(client, db, AI_TT, SITE_PARAMS, sport_id=10, days_back=1, now=now,
                                       parse_fn=parse_table_tennis_score)

    assert n_stored == len(iter_windows(end=now, days_back=1))
    assert all(sid == 10 for sid in sent_sport_ids)  # jamais le 103 par défaut de Mortal Kombat
    assert await db.fetchval("SELECT count(*) FROM results WHERE game_id = 999") == 1
    assert await db.fetchval("SELECT count(*) FROM round_results WHERE game_id = 999") == 0
