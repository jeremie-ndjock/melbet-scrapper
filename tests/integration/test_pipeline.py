"""Rejeu d'un match complet (enregistrement réel de la reconnaissance) à travers tout le pipeline :
validation -> normalisation -> détection de changement -> écriture. Critère de l'étape 4
(docs/architecture.md, §14) : un match complet stocké, cohérent avec l'enregistrement de référence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from collector.dedupe import ChangeDetector
from collector.normalize import flatten_game
from collector.pipeline import process_cycle
from collector.sources.v3.models import GamesByChampResponse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def replay(db, fixture: dict) -> tuple[ChangeDetector, list]:
    detector = ChangeDetector()
    results = []
    for record in fixture["records"]:
        body = {"liga": {"id": fixture["league_id"], "name": "x"}, "gamesCount": 1, "games": [record["game"]]}
        response = GamesByChampResponse.model_validate(body)
        collected_at = datetime.fromtimestamp(record["t"], tz=timezone.utc)
        result = await process_cycle(
            db, detector, response,
            league_id=fixture["league_id"], collected_at=collected_at, latency_ms=700,
        )
        results.append(result)
    return detector, results


async def test_full_match_replay_mortal_kombat_x(db):
    fixture = load_fixture("mkx_full_match_754878439.json")
    game_id = fixture["game_id"]

    detector, results = await replay(db, fixture)

    total_selections_per_cycle = sum(r.n_games for r in results)
    assert total_selections_per_cycle == len(fixture["records"])  # un seul match par relevé, comme en direct

    n_rows = await db.fetchval("SELECT count(*) FROM odds_snapshots WHERE game_id = $1", game_id)
    n_events_total = sum(len(flatten_game(GamesByChampResponse.model_validate(
        {"liga": {"id": 1, "name": "x"}, "gamesCount": 1, "games": [r["game"]]}
    ).games[0])) for r in fixture["records"])

    # Option A : nettement moins de lignes que si chaque relevé écrivait toutes ses sélections (grille complète).
    assert 0 < n_rows < n_events_total * 0.15

    # Le match et son détail sont bien enregistrés.
    event = await db.fetchrow("SELECT p1_name, p2_name, league_id, status FROM events WHERE game_id = $1", game_id)
    assert event["league_id"] == fixture["league_id"]
    assert event["status"] == "finished"  # dernier relevé du match : « Jeu terminé »

    n_states = await db.fetchval("SELECT count(*) FROM game_state WHERE game_id = $1", game_id)
    assert n_states > 0

    # L'état final en base est cohérent avec le dernier relevé de l'enregistrement.
    #
    # `odds_latest` garde la dernière ligne connue de TOUTES les sélections jamais vues pour ce
    # match, y compris celles retirées en cours de route (odds NULL) : ce n'est donc pas exactement
    # l'ensemble des sélections actives dans le dernier relevé. On vérifie séparément les deux
    # invariants : (1) toute sélection encore active a la bonne cote et le bon verrou ; (2) toute
    # sélection connue mais absente du dernier relevé a bien été marquée comme retirée (odds NULL).
    last_game = fixture["records"][-1]["game"]
    expected_final = flatten_game(GamesByChampResponse.model_validate(
        {"liga": {"id": 1, "name": "x"}, "gamesCount": 1, "games": [last_game]}
    ).games[0])
    latest_rows = await db.fetch(
        "SELECT g, t, param, odds, blocked FROM odds_latest WHERE game_id = $1", game_id
    )
    latest_by_key = {(r["g"], r["t"], r["param"]): r for r in latest_rows}

    assert set(expected_final) <= set(latest_by_key)  # toute sélection active est bien en base
    for key, expected in expected_final.items():
        assert latest_by_key[key]["odds"] == expected.odds
        assert latest_by_key[key]["blocked"] == expected.blocked
    for key in set(latest_by_key) - set(expected_final):
        assert latest_by_key[key]["odds"] is None  # sélection connue, absente à la fin : retirée


async def test_full_match_replay_mortal_kombat_3_with_extra_markets(db):
    """Mortal Kombat 3 expose des groupes de marchés absents de Mortal Kombat X
    (Mercy, Babality, Animality, Totaux supplémentaires) : ils doivent être stockés sans erreur."""
    fixture = load_fixture("mk3_full_match_754887371.json")
    game_id = fixture["game_id"]

    await replay(db, fixture)

    groups = {r["g"] for r in await db.fetch("SELECT DISTINCT g FROM odds_snapshots WHERE game_id = $1", game_id)}
    assert 10533 in groups or 912 in groups  # au moins un marché propre à Mortal Kombat 3 est bien stocké

    n_rows = await db.fetchval("SELECT count(*) FROM odds_snapshots WHERE game_id = $1", game_id)
    assert n_rows > 0


async def test_replay_is_idempotent(db):
    """Rejouer exactement le même enregistrement (ex. redémarrage après plantage) ne crée aucun doublon."""
    fixture = load_fixture("mkx_full_match_754878439.json")
    await replay(db, fixture)
    n_first = await db.fetchval("SELECT count(*) FROM odds_snapshots")

    # Nouveau détecteur (comme après un redémarrage sans préchargement) : les mêmes lignes sont
    # recalculées, mais la contrainte d'unicité de la base absorbe les doublons exacts.
    await replay(db, fixture)
    n_second = await db.fetchval("SELECT count(*) FROM odds_snapshots")
    assert n_second == n_first


async def test_preload_avoids_rewriting_known_state(db):
    """Après un redémarrage, précharger l'état depuis la base évite de réécrire ce qui est déjà connu."""
    from collector.pipeline import preload_from_db

    fixture = load_fixture("mkx_full_match_754878439.json")
    half = len(fixture["records"]) // 2
    first_half = {**fixture, "records": fixture["records"][:half]}
    await replay(db, first_half)
    n_after_half = await db.fetchval("SELECT count(*) FROM odds_snapshots")

    # Nouveau processus : détecteur vide, mais préchargé depuis la base avant de reprendre le flux.
    detector = ChangeDetector()
    await preload_from_db(db, detector, [fixture["game_id"]])
    last_known_record = fixture["records"][half - 1]  # dernier relevé déjà écrit dans la première moitié
    for record in [last_known_record]:  # rejouer un relevé déjà connu, identique à l'état préchargé
        body = {"liga": {"id": fixture["league_id"], "name": "x"}, "gamesCount": 1, "games": [record["game"]]}
        response = GamesByChampResponse.model_validate(body)
        await process_cycle(db, detector, response, league_id=fixture["league_id"],
                             collected_at=datetime.fromtimestamp(record["t"], tz=timezone.utc), latency_ms=700)

    n_after_replay_of_known_state = await db.fetchval("SELECT count(*) FROM odds_snapshots")
    assert n_after_replay_of_known_state == n_after_half  # rien de nouveau : déjà connu via le préchargement
