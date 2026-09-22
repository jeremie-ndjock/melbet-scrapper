"""Enchaînement d'un cycle de collecte pour une ligue : réponse validée -> écriture en base.

Séparé du transport pour être testable sans réseau (rejeu des enregistrements de la reconnaissance,
voir tests/fixtures/) et réutilisé tel quel par le futur ordonnanceur (étape 6).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from .dedupe import ChangeDetector
from .normalize import flatten_game
from .sources.v3.models import GamesByChampResponse
from .storage import writer

log = logging.getLogger("collector.pipeline")


@dataclass(frozen=True)
class CycleResult:
    n_games: int
    n_rows_written: int


def _status_for(game, now: datetime) -> str:
    if game.scores.currentPeriodName == "Jeu terminé":
        return "finished"
    if game.startTs > now.timestamp():
        return "scheduled"
    return "live"


async def process_cycle(
    conn: asyncpg.Connection,
    detector: ChangeDetector,
    response: GamesByChampResponse,
    *,
    league_id: int,
    collected_at: datetime,
    latency_ms: int | None,
    source: int = writer.SOURCE_V3,
) -> CycleResult:
    """Traite un relevé ``gamesByChamp`` déjà validé : écrit les matchs, leur état, et les
    changements de cotes. Une transaction par cycle : le relevé est écrit en entier ou pas du tout.

    ``source`` doit refléter la source réellement interrogée pour ce relevé (v3 ou secours) : les
    cotes de la source de secours ne doivent jamais être marquées comme venant de la source
    principale, sous peine de fausser toute analyse ultérieure distinguant les deux.
    """
    n_rows = 0
    async with conn.transaction():
        for game in response.games:
            ts_server = datetime.fromtimestamp(game.updateTs, tz=timezone.utc)
            status = _status_for(game, collected_at)

            await writer.upsert_event(conn, game, league_id, status=status, seen_at=collected_at)
            await writer.insert_game_state(conn, game, ts_server=ts_server, collected_at=collected_at)

            current = flatten_game(game)
            changes = detector.diff(game.id, current)
            n_rows += await writer.write_odds_batch(
                conn, changes, league_id=league_id, ts_server=ts_server,
                collected_at=collected_at, latency_ms=latency_ms, source=source,
            )

    return CycleResult(n_games=len(response.games), n_rows_written=n_rows)


async def preload_from_db(conn: asyncpg.Connection, detector: ChangeDetector, game_ids: list[int]) -> None:
    """Recharge l'état des sélections des matchs donnés depuis la base (reprise après redémarrage)."""
    by_game = await writer.load_latest_odds(conn, game_ids)
    for game_id, rows in by_game.items():
        detector.preload(game_id, rows)
