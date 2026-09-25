"""Extraction de `forecasting` contre une vraie base migrée : lecture seule effective, filtrage
par ligue (les sets d'AI Table Tennis partagent `round_results`), lignes « retirée » conservées."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pandas as pd
import pytest

from forecasting import extract

MKX, AI_TT = 1252965, 3066896
T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


async def _seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.executemany(
            "INSERT INTO results (game_id, league_id, p1_id, p2_id, p1_name, p2_name, final_score1, final_score2,"
            " winner, score_raw, date_start) VALUES ($1, $2, 1, 2, 'Goro', 'Ermac', 5, 3, 1, '5:3(...)', $3)",
            [(10, MKX, T0), (11, MKX, T0 + timedelta(minutes=15)), (20, AI_TT, T0)],
        )
        await conn.executemany(
            "INSERT INTO round_results (game_id, round_no, winner, seconds, finish_code) VALUES ($1, $2, $3, $4, $5)",
            [(10, 1, 1, 31, "F"), (10, 2, 2, None, "R"), (11, 1, 1, None, "B"), (20, 1, 2, None, None)],
        )
        await conn.execute(
            "INSERT INTO events (game_id, league_id, p1_id, p2_id, p1_name, p2_name, start_ts, status, first_seen,"
            " last_seen, finished_at) VALUES (10, $1, 1, 2, 'Goro', 'Ermac', $2, 'finished', $2, $3, $3)",
            MKX, T0, T0 + timedelta(minutes=12),
        )
        await conn.executemany(
            "INSERT INTO odds_snapshots (ts_server, game_id, g, t, param, league_id, collected_at, odds, blocked, source)"
            " VALUES ($1, 10, $2, $3, $4, $5, $1, $6, $7, 1)",
            [(T0, 1050, 2140, 1, MKX, 1.5, False), (T0, 1050, 2141, 1, MKX, 2.6, False),
             (T0 + timedelta(seconds=40), 1050, 2140, 1, MKX, None, False),
             (T0 + timedelta(seconds=5), 1050, 2141, 1, MKX, 2.7, True),
             (T0, 1066, 4057, 1, MKX, 2.1, False)],
        )
        await conn.executemany(
            "INSERT INTO game_state (game_id, ts_server, collected_at, period, score1, score2) VALUES (10, $1, $1, 1, $2, $3)",
            [(T0 + timedelta(seconds=30), 0, 0), (T0 + timedelta(seconds=90), 1, 0), (T0 + timedelta(seconds=95), 1, 0),
             (T0 + timedelta(seconds=200), 1, 1)],
        )
    finally:
        await conn.close()


async def test_extraction_filters_by_league_and_keeps_withdrawn_quotes(db_dsn):
    await _seed(db_dsn)
    since, until = T0 - timedelta(days=1), T0 + timedelta(days=1)
    conn = await extract.connect_readonly(db_dsn)
    try:
        matches, rounds = await extract.load_history(conn, [MKX], since, until)
        events = await extract.load_events(conn, [MKX], since, until)
        odds = await extract.load_odds(conn, MKX, [1050], since, until)
        ends = await extract.load_round_ends(conn, MKX, since, until)
        first = await extract.first_odds_ts(conn, MKX, 1050, until)
    finally:
        await conn.close()

    assert sorted(matches["game_id"]) == [10, 11]
    assert str(matches["date_start"].dt.tz) == "UTC"
    assert sorted(rounds["game_id"].unique()) == [10, 11]  # le set d'AI Table Tennis est exclu
    assert rounds.loc[(rounds["game_id"] == 10) & (rounds["round_no"] == 1), "seconds"].iloc[0] == 31
    assert events.loc[0, "finished_at"] - events.loc[0, "start_ts"] == pd.Timedelta(minutes=12)
    assert len(odds) == 4 and odds["odds"].isna().sum() == 1  # g=1066 non demandé, retrait conservé
    assert odds["blocked"].dtype == bool and odds["blocked"].sum() == 1
    assert ends.set_index("round_no")["round_end"].to_dict() == {
        1: pd.Timestamp(T0 + timedelta(seconds=90)), 2: pd.Timestamp(T0 + timedelta(seconds=200))}
    assert first == T0


async def test_gather_assembles_cleaned_data_and_quality_report(db_dsn):
    from forecasting.__main__ import gather

    await _seed(db_dsn)
    data, report, lag = await gather(db_dsn, pd.Timestamp(T0 + timedelta(days=1)))
    assert set(data) == {MKX}  # MK3 : aucune cote, donc pas de fenêtre d'évaluation
    assert data[MKX]["w_start"] == pd.Timestamp(T0)
    assert len(data[MKX]["odds"]) == 4 and not data[MKX]["round_ends"].empty  # g=1066 non demandé pour MKX
    q = report[str(MKX)]
    assert q["matchs_bruts"] == 2 and q["exclus_score_incoherent"] == 2  # 2 manches pour un score de 5-3
    assert lag == pd.Timedelta(minutes=18)  # un seul match de 12 min : le minimum s'applique
    assert report["delai_disponibilite"]["p99_duree_match_s"] == pytest.approx(720)


async def test_readonly_session_refuses_any_write(db_dsn):
    conn = await extract.connect_readonly(db_dsn)
    try:
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute("INSERT INTO markets_dict (group_id, type_id, group_label, type_label) VALUES (1, 1, 'x', 'y')")
    finally:
        await conn.close()


async def test_round_ends_empty_without_live_data(db_dsn):
    conn = await extract.connect_readonly(db_dsn)
    try:
        ends = await extract.load_round_ends(conn, MKX, T0, T0 + timedelta(days=1))
    finally:
        await conn.close()
    assert ends.empty and list(ends.columns) == ["game_id", "round_no", "round_end"]
