"""Écriture en base d'un relevé normalisé : match, état du match, cotes.

Toutes les requêtes utilisées (voir queries.py) sont idempotentes : rejouer le même relevé ne crée
aucun doublon et ne fait jamais reculer un état.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from ..normalize import SnapshotRow
from ..sources.v3.models import Game
from . import queries

SOURCE_V3 = 1
SOURCE_LEGACY = 2


async def upsert_event(conn: asyncpg.Connection, game: Game, league_id: int, *, status: str, seen_at: datetime) -> None:
    await conn.execute(
        queries.UPSERT_EVENT,
        game.id,
        league_id,
        game.video.id if game.video else None,
        game.num,
        game.opponent1.opponent_id,
        game.opponent2.opponent_id,
        game.opponent1.display_name,
        game.opponent2.display_name,
        datetime.fromtimestamp(game.startTs, tz=timezone.utc),
        status,
        seen_at,
        seen_at if status == "finished" else None,
    )


async def insert_game_state(conn: asyncpg.Connection, game: Game, *, ts_server: datetime, collected_at: datetime) -> None:
    scores = game.scores
    await conn.execute(
        queries.INSERT_GAME_STATE,
        game.id,
        ts_server,
        collected_at,
        scores.currentPeriod,
        scores.fullScoreDetail.scoreOpp1,
        scores.fullScoreDetail.scoreOpp2,
        scores.timer.timeSec,
        scores.currentPeriodName,
    )


async def write_odds_batch(
    conn: asyncpg.Connection,
    rows: list[SnapshotRow],
    *,
    league_id: int,
    ts_server: datetime,
    collected_at: datetime,
    source: int = SOURCE_V3,
    latency_ms: int | None,
) -> int:
    """Écrit un lot de lignes de cotes. Retourne le nombre de lignes effectivement insérées
    (les doublons exacts, en théorie impossibles à ``ts_server`` fixé, sont ignorés sans erreur)."""
    if not rows:
        return 0
    args = [
        (
            ts_server, row.game_id, row.g, row.t, row.param, row.sub_game_id, league_id, collected_at,
            row.odds, row.blocked, row.is_center, row.round_no, row.line, source, latency_ms,
        )
        for row in rows
    ]
    await conn.executemany(queries.INSERT_ODDS_SNAPSHOT, args)
    return len(args)


async def load_latest_odds(conn: asyncpg.Connection, game_ids: list[int]) -> dict[int, list[SnapshotRow]]:
    """Relit le dernier état connu des sélections des matchs donnés (reprise après redémarrage)."""
    if not game_ids:
        return {}
    rows = await conn.fetch(queries.LATEST_ODDS_FOR_GAMES, game_ids)
    by_game: dict[int, list[SnapshotRow]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(
            SnapshotRow(r["game_id"], r["g"], r["t"], r["param"], r["sub_game_id"],
                        r["odds"], r["blocked"], r["is_center"], None, None)
        )
    return by_game
