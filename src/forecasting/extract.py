"""Extraction depuis la base de production, en session strictement en lecture seule.

Les requêtes sont toujours bornées (ligue, marché, plage horaire) : les limites de ressources
du conteneur d'entraînement ne protègent pas la base elle-même, partagée avec le collecteur.
"""
from __future__ import annotations

import io

import asyncpg
import pandas as pd

READONLY_SETTINGS = {
    "default_transaction_read_only": "on",
    "statement_timeout": "120s",
    "idle_in_transaction_session_timeout": "60s",
    "application_name": "forecasting",
    "jit": "off",
}

MATCHES_SQL = """
SELECT game_id, league_id, p1_id, p2_id, p1_name, p2_name, final_score1, final_score2, winner, date_start
FROM results WHERE league_id = ANY($1::int[]) AND date_start >= $2 AND date_start < $3
"""
# La jointure sur `results` exclut les sets d'AI Table Tennis, qui partagent la table.
ROUNDS_SQL = """
SELECT rr.game_id, rr.round_no, rr.winner, rr.seconds, rr.finish_code, rr.finish_di, rr.fw
FROM round_results rr JOIN results r ON r.game_id = rr.game_id
WHERE r.league_id = ANY($1::int[]) AND r.date_start >= $2 AND r.date_start < $3
"""
EVENTS_SQL = """
SELECT game_id, league_id, p1_id, p2_id, start_ts, finished_at
FROM events WHERE league_id = ANY($1::int[]) AND start_ts >= $2 AND start_ts < $3
"""
# Garde les lignes « retirée » (odds NULL) et suspendues : indispensables pour savoir quand une
# cote n'était plus jouable.
ODDS_SQL = """
SELECT game_id, g, t, param, odds, blocked, ts_server
FROM odds_snapshots
WHERE league_id = $1 AND g = ANY($2::int[]) AND sub_game_id = 0 AND ts_server >= $3 AND ts_server < $4
"""
ROUND_END_SQL = """
SELECT gs.game_id, gs.ts_server, gs.score1 + gs.score2 AS rounds_done
FROM game_state gs JOIN events e ON e.game_id = gs.game_id
WHERE e.league_id = $1 AND gs.ts_server >= $2 AND gs.ts_server < $3
"""
FIRST_ODDS_SQL = "SELECT min(ts_server) FROM odds_snapshots WHERE league_id = $1 AND g = $2 AND ts_server < $3"

_DATES = {"date_start", "start_ts", "finished_at", "ts_server"}
_BOOLS = {"blocked"}


async def connect_readonly(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn, timeout=10, server_settings=READONLY_SETTINGS)


async def fetch_df(conn: asyncpg.Connection, sql: str, *args) -> pd.DataFrame:
    """Résultat d'une requête sous forme de DataFrame, transféré en CSV par COPY (évite de créer
    des centaines de milliers d'objets Python intermédiaires)."""
    buf = io.BytesIO()
    await conn.copy_from_query(sql.strip(), *args, output=buf, format="csv", header=True)
    buf.seek(0)
    df = pd.read_csv(buf, low_memory=False)
    for col in df.columns:
        if col in _DATES:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
        elif col in _BOOLS:
            df[col] = df[col].map({"t": True, "f": False}).astype(bool)
    return df


async def load_history(conn, leagues: list[int], since, until) -> tuple[pd.DataFrame, pd.DataFrame]:
    matches = await fetch_df(conn, MATCHES_SQL, leagues, since, until)
    rounds = await fetch_df(conn, ROUNDS_SQL, leagues, since, until)
    return matches, rounds


async def load_events(conn, leagues: list[int], since, until) -> pd.DataFrame:
    return await fetch_df(conn, EVENTS_SQL, leagues, since, until)


async def load_odds(conn, league_id: int, groups: list[int], since, until) -> pd.DataFrame:
    return await fetch_df(conn, ODDS_SQL, league_id, groups, since, until)


async def load_round_ends(conn, league_id: int, since, until) -> pd.DataFrame:
    """Heure de fin de chaque manche vue en direct : premier relevé où le total des manches
    jouées atteint N (sert à vérifier qu'aucune cote retenue n'est postérieure à la manche)."""
    gs = await fetch_df(conn, ROUND_END_SQL, league_id, since, until)
    if gs.empty:
        return pd.DataFrame(columns=["game_id", "round_no", "round_end"])
    gs = gs[gs["rounds_done"] > 0]
    ends = gs.groupby(["game_id", "rounds_done"])["ts_server"].min().reset_index()
    return ends.rename(columns={"rounds_done": "round_no", "ts_server": "round_end"})


async def first_odds_ts(conn, league_id: int, g: int, until):
    return await conn.fetchval(FIRST_ODDS_SQL, league_id, g, until)
