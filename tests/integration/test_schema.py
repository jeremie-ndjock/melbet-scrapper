"""Tests du schéma : contraintes, idempotence des écritures, hypertables et compression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from collector.storage import queries

MK_X = 1252965
MK_3 = 2282406
T0 = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


async def add_event(db, game_id: int = 1, league_id: int = MK_X, status: str = "live"):
    await db.execute(
        queries.UPSERT_EVENT, game_id, league_id, "xgame7_1", 100, 1, 2, "Liu Kang", "Kenshi",
        T0, status, T0, None,
    )


async def add_odds(db, ts: datetime, *, game_id: int = 1, g: int = 1, t: int = 1, param: str = "0",
                   sub_game_id: int = 0, odds: str | None = "1.500", blocked: bool = False, source: int = 1):
    await db.execute(
        queries.INSERT_ODDS_SNAPSHOT, ts, game_id, g, t, Decimal(param), sub_game_id, MK_X, ts,
        None if odds is None else Decimal(odds), blocked, False, None, None, source, 120,
    )


async def count_odds(db) -> int:
    return await db.fetchval("SELECT count(*) FROM odds_snapshots")


# --------------------------------------------------------------------------- référence

TT_PRAGUE = 3066896
TT_GOA = 3066897


async def test_leagues_are_seeded(db):
    rows = await db.fetch("SELECT league_id, name FROM leagues ORDER BY league_id")
    assert {r["league_id"]: r["name"] for r in rows} == {
        MK_X: "Mortal Kombat X", MK_3: "Mortal Kombat 3",
        TT_PRAGUE: "AI Table Tennis Prague", TT_GOA: "AI Table Tennis Goa",
    }


# --------------------------------------------------------------------------- cotes

async def test_odds_insert_is_idempotent(db):
    await add_event(db)
    await add_odds(db, T0)
    await add_odds(db, T0)  # rejeu exact
    assert await count_odds(db) == 1


async def test_odds_history_is_never_overwritten(db):
    await add_event(db)
    await add_odds(db, T0, odds="1.500")
    await add_odds(db, T0 + timedelta(seconds=5), odds="1.800")
    await add_odds(db, T0, odds="9.999")  # même clé avec une autre valeur : ignorée
    rows = await db.fetch("SELECT odds FROM odds_snapshots ORDER BY ts_server")
    assert [r["odds"] for r in rows] == [Decimal("1.500"), Decimal("1.800")]


async def test_removal_marker_and_default_param(db):
    await add_event(db)
    await db.execute(
        """INSERT INTO odds_snapshots (ts_server, game_id, g, t, league_id, collected_at, odds, source)
           VALUES ($1, 1, 17, 9, $2, $1, NULL, 1)""",
        T0, MK_X,
    )
    row = await db.fetchrow("SELECT odds, param, blocked, is_center FROM odds_snapshots")
    assert row["odds"] is None          # sélection retirée
    assert row["param"] == 0            # valeur par défaut : « sans paramètre »
    assert row["blocked"] is False and row["is_center"] is False


async def test_odds_latest_view_returns_last_row_per_selection(db):
    await add_event(db)
    await add_odds(db, T0, g=17, t=9, param="7.5", odds="1.400")
    await add_odds(db, T0 + timedelta(seconds=40), g=17, t=9, param="7.5", odds="1.700", blocked=True)
    await add_odds(db, T0 + timedelta(seconds=80), g=17, t=9, param="7.5", odds=None)   # retirée
    await add_odds(db, T0 + timedelta(seconds=10), g=1, t=1, odds="2.000")

    rows = await db.fetch(queries.LATEST_ODDS_FOR_GAMES, [1])
    by_key = {(r["g"], r["t"]): r for r in rows}
    assert len(rows) == 2
    assert by_key[(17, 9)]["odds"] is None                         # le dernier état est le retrait
    assert by_key[(1, 1)]["odds"] == Decimal("2.000")


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"odds": "0"}, asyncpg.CheckViolationError),          # cote non positive
        ({"odds": "-1.5"}, asyncpg.CheckViolationError),
        ({"source": 3}, asyncpg.CheckViolationError),          # source inconnue
        ({"game_id": 999}, asyncpg.ForeignKeyViolationError),  # match inconnu
    ],
)
async def test_odds_constraints(db, kwargs, error):
    await add_event(db)
    with pytest.raises(error):
        await add_odds(db, T0, **kwargs)


async def test_same_selection_in_different_subgames_are_stored_separately(db):
    """Découvert avec AI Table Tennis (Memoire.md, section 27 ; migration 009) : (g, t, param)
    seul ne suffit pas à identifier une sélection quand les marchés sont répartis par sous-match
    (un set) — sans sub_game_id dans la clé, la deuxième écriture écraserait silencieusement la
    première plutôt que de créer une deuxième ligne."""
    await add_event(db)
    await add_odds(db, T0, g=2, t=7, param="2.5", sub_game_id=111, odds="1.360")
    await add_odds(db, T0, g=2, t=7, param="2.5", sub_game_id=222, odds="1.900")
    assert await count_odds(db) == 2
    rows = await db.fetch("SELECT sub_game_id, odds FROM odds_snapshots ORDER BY sub_game_id")
    assert [(r["sub_game_id"], r["odds"]) for r in rows] == [(111, Decimal("1.360")), (222, Decimal("1.900"))]


# --------------------------------------------------------------------------- matchs

async def test_event_upsert_keeps_first_seen_and_never_moves_backwards(db):
    await add_event(db, status="live")
    later = T0 + timedelta(minutes=10)
    earlier = T0 - timedelta(minutes=10)

    await db.execute(queries.UPSERT_EVENT, 1, MK_X, "x", 1, 1, 2, "A", "B", T0, "finished", later, later)
    await db.execute(queries.UPSERT_EVENT, 1, MK_X, "x", 1, 1, 2, "A", "B", T0, "live", earlier, None)  # rejeu ancien

    row = await db.fetchrow("SELECT status, first_seen, last_seen, finished_at FROM events WHERE game_id = 1")
    assert row["status"] == "finished"          # ne redevient pas « live »
    assert row["first_seen"] == T0              # ne change jamais
    assert row["last_seen"] == later            # ne recule jamais
    assert row["finished_at"] == later


async def test_event_constraints(db):
    with pytest.raises(asyncpg.CheckViolationError):        # statut inconnu
        await add_event(db, status="zombie")
    with pytest.raises(asyncpg.ForeignKeyViolationError):   # ligue inconnue
        await add_event(db, league_id=42)
    with pytest.raises(asyncpg.CheckViolationError):        # last_seen avant first_seen
        await db.execute(
            "INSERT INTO events (game_id, league_id, p1_name, p2_name, start_ts, first_seen, last_seen) "
            "VALUES (7, $1, 'A', 'B', $2, $2, $3)", MK_X, T0, T0 - timedelta(seconds=1),
        )


async def test_game_state_is_append_only(db):
    await add_event(db)
    args = (1, T0, T0, 2, 1, 0, 65, "2ème round")
    await db.execute(queries.INSERT_GAME_STATE, *args)
    await db.execute(queries.INSERT_GAME_STATE, *args)
    assert await db.fetchval("SELECT count(*) FROM game_state") == 1
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(queries.INSERT_GAME_STATE, 1, T0 + timedelta(seconds=5), T0, 2, -1, 0, 1, "x")


# --------------------------------------------------------------------------- résultats

async def test_round_results_are_completed_without_overwriting(db):
    # 1) tableau des rounds : durée et libellé de finish
    await db.execute(queries.UPSERT_ROUND_RESULT, 5, 1, 2, 30, "Regular", None, "0", True, None, None)
    # 2) résultats officiels : code de finish et Mercy, plus une valeur contradictoire pour la durée
    await db.execute(queries.UPSERT_ROUND_RESULT, 5, 1, 2, 99, "Autre", "R", None, None, False, False)

    row = await db.fetchrow("SELECT * FROM round_results WHERE game_id = 5 AND round_no = 1")
    assert row["seconds"] == 30 and row["finish_di"] == "Regular"     # non écrasés
    assert row["finish_code"] == "R" and row["mercy_p1"] is False     # complétés
    assert row["wt"] == "0" and row["fw"] is True


async def test_result_insert_is_idempotent_and_constrained(db):
    args = (77, MK_3, 1, 2, "Baraka", "Rain", 5, 3, 1, "5:3(...)", T0, 3)
    await db.execute(queries.INSERT_RESULT, *args)
    await db.execute(queries.INSERT_RESULT, *args)
    assert await db.fetchval("SELECT count(*) FROM results") == 1
    with pytest.raises(asyncpg.CheckViolationError):          # gagnant incohérent avec un score non nul
        await db.execute(queries.INSERT_RESULT, 78, MK_3, 1, 2, "A", "B", 5, 3, None, "x", T0, 3)
    with pytest.raises(asyncpg.CheckViolationError):          # score négatif
        await db.execute(queries.INSERT_RESULT, 79, MK_3, 1, 2, "A", "B", -1, 3, 2, "x", T0, 3)


async def test_result_insert_allows_a_tied_final_score(db):
    """Observé en réel le 2026-09-21 (étape 6) : un match Mortal Kombat 3 terminé 2:2. La
    migration 005 corrige l'hypothèse initiale (aucune égalité possible), fausse en pratique."""
    await db.execute(queries.INSERT_RESULT, 80, MK_3, 1, 2, "A", "B", 2, 2, None, "2:2(...)", T0, 3)
    row = await db.fetchrow("SELECT final_score1, final_score2, winner FROM results WHERE game_id = 80")
    assert (row["final_score1"], row["final_score2"], row["winner"]) == (2, 2, None)
    with pytest.raises(asyncpg.CheckViolationError):  # un score à égalité ne peut pas avoir de gagnant
        await db.execute(queries.INSERT_RESULT, 81, MK_3, 1, 2, "A", "B", 2, 2, 1, "2:2(...)", T0, 3)


# --------------------------------------------------------------------------- exploitation

async def test_collection_log_and_dead_letter(db):
    await db.execute(
        "INSERT INTO collection_log (ts, league_id, source, endpoint, ok, http_status, latency_ms, n_games, n_rows_written) "
        "VALUES ($1, $2, 1, 'gamesByChamp', true, 200, 800, 4, 12)", T0, MK_X,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO collection_log (ts, source, endpoint, ok) VALUES ($1, 9, 'x', true)", T0)

    corrupt = '{"games": [ {"id": 1, '           # JSON tronqué : ne pourrait pas être stocké en jsonb
    await db.execute(
        "INSERT INTO dead_letter (source, endpoint, league_id, error, payload, payload_bytes) VALUES (1, 'gamesByChamp', $1, $2, $3, $4)",
        MK_X, "JSONDecodeError", corrupt, len(corrupt),
    )
    assert await db.fetchval("SELECT payload FROM dead_letter") == corrupt


async def test_checkpoints_upsert(db):
    upsert = ("INSERT INTO checkpoints (worker, key, value) VALUES ($1, $2, $3::jsonb) "
              "ON CONFLICT (worker, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()")
    await db.execute(upsert, "results", "cursor", '{"last_window": 1789991700}')
    await db.execute(upsert, "results", "cursor", '{"last_window": 1789992000}')
    assert await db.fetchval("SELECT count(*) FROM checkpoints") == 1
    assert await db.fetchval("SELECT value->>'last_window' FROM checkpoints") == "1789992000"


# --------------------------------------------------------------------------- Timescale

async def test_hypertables_exist_with_expected_chunk_interval(db):
    rows = await db.fetch(
        "SELECT hypertable_name, EXTRACT(EPOCH FROM time_interval) AS secs "
        "FROM timescaledb_information.dimensions WHERE dimension_type = 'Time'"
    )
    intervals = {r["hypertable_name"]: r["secs"] for r in rows}
    assert intervals["odds_snapshots"] == 7 * 86400
    assert intervals["collection_log"] == 30 * 86400


async def test_compression_is_configured_with_policies(db):
    jobs = await db.fetch(
        "SELECT hypertable_name, proc_name, config::text AS config FROM timescaledb_information.jobs "
        "WHERE hypertable_name IN ('odds_snapshots', 'collection_log') AND proc_name ILIKE 'policy_%'"
    )
    by_table = {r["hypertable_name"]: r for r in jobs}
    assert set(by_table) == {"odds_snapshots", "collection_log"}
    assert "7 days" in by_table["odds_snapshots"]["config"]
    assert "14 days" in by_table["collection_log"]["config"]


async def test_old_chunks_compress_without_losing_data_and_stay_idempotent(db):
    await add_event(db)
    old = T0 - timedelta(days=40)
    await add_odds(db, old, g=17, t=9, param="7.5", odds="1.400")
    await add_odds(db, old + timedelta(seconds=40), g=17, t=9, param="7.5", odds="1.750")
    await add_odds(db, datetime.now(timezone.utc), g=1, t=1, odds="2.000")

    chunks = await db.fetchval("SELECT count(*) FROM show_chunks('odds_snapshots')")
    assert chunks == 2                                       # deux semaines distinctes -> deux chunks

    await db.execute("SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('odds_snapshots', older_than => INTERVAL '7 days') c")

    assert await count_odds(db) == 3                          # rien de perdu
    rows = await db.fetch("SELECT odds FROM odds_snapshots WHERE g = 17 ORDER BY ts_server")
    assert [r["odds"] for r in rows] == [Decimal("1.400"), Decimal("1.750")]

    await add_odds(db, old, g=17, t=9, param="7.5", odds="1.400")   # rejeu dans un chunk compressé
    assert await count_odds(db) == 3                                # toujours aucun doublon
