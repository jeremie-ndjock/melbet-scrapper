"""Tests de la source de secours (legacy) : parseur et adaptateur, à partir des réponses réellement
téléchargées lors de la reconnaissance (tests/fixtures/legacy/)."""
from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

from collector.normalize import flatten_game
from collector.sources.legacy.adapter import _decode_legacy_param, adapt_game, build_response
from collector.sources.legacy.models import ChampGame, ChampZipEnvelope, GameZipEnvelope

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legacy"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_champ_zip_envelope_parses_the_real_response():
    envelope = ChampZipEnvelope.model_validate(load("champ_zip.json"))
    assert envelope.Success is True
    game = envelope.Value.G[0]
    assert game.I == 754874259 and game.O1 == "ALIEN" and game.O2 == "Shinnok"
    assert game.SC.FS.S1 == 1 and game.SC.FS.S2 == 1 and game.SC.CPS == "3ème round"


def test_game_zip_envelope_parses_the_real_response():
    envelope = GameZipEnvelope.model_validate(load("game_zip.json"))
    assert envelope.Success is True
    detail = envelope.Value
    assert detail.I == 754862258 and detail.U == 1789988267
    groups = {g.G for g in detail.GE}
    assert {1050, 1074} <= groups  # Victoire dans le Round, Durée du Round : bien présents


def test_decode_legacy_param_round_only_group():
    assert _decode_legacy_param(1050, 8) == ["8"]  # groupe « par round »


def test_decode_legacy_param_round_and_line_group():
    # Exemple réel (game_zip.json, groupe 1074) : P=800.255 -> round 8, ligne 25.5
    # (round = P // 100 ; ligne = (P - round*100) * 100, voir Memoire.md section 15).
    assert _decode_legacy_param(1074, 800.255) == ["8", "25.5"]
    # Exemple réel confirmé côté v3 (même encodage) : 600.175 -> round 6, ligne 17.5.
    assert _decode_legacy_param(1074, 600.175) == ["6", "17.5"]


def test_decode_legacy_param_line_only_group_defaults_to_line():
    assert _decode_legacy_param(17, 7.5) == ["7.5"]


def test_decode_legacy_param_none_yields_no_params():
    assert _decode_legacy_param(1, None) is None


def test_adapt_game_combines_champ_and_detail_and_matches_v3_shape():
    champ_game = ChampGame.model_validate(load("champ_zip.json")["Value"]["G"][0])
    detail_env = GameZipEnvelope.model_validate(load("game_zip.json"))
    # Le détail réel correspond à un autre match ; on l'associe ici uniquement pour vérifier que
    # l'adaptateur produit un `Game` v3 exploitable par le normaliseur, quel que soit le contenu.
    game = adapt_game(champ_game, detail_env.Value)

    assert game.id == champ_game.I
    assert game.opponent1.display_name == "ALIEN" and game.opponent2.display_name == "Shinnok"
    assert game.updateTs == detail_env.Value.U  # le détail, plus frais, prime sur la fiche de ligue
    assert game.scores.fullScoreDetail.scoreOpp1 == detail_env.Value.SC.FS.S1

    rows = flatten_game(game)
    assert len(rows) > 0
    sample_key = next(iter(rows))
    assert isinstance(rows[sample_key].param, Decimal)  # identité de sélection exploitable normalement


def test_adapt_game_without_detail_falls_back_to_champ_score_and_no_odds():
    """Si la deuxième requête (détail) échoue, le match reste rapporté (score de la fiche de
    ligue), simplement sans cotes pour ce cycle — mieux que de perdre le match entièrement."""
    champ_game = ChampGame.model_validate(load("champ_zip.json")["Value"]["G"][0])
    game = adapt_game(champ_game, None)
    assert game.updateTs == champ_game.S
    assert game.eventGroups == []
    assert flatten_game(game) == {}


def test_build_response_matches_v3_response_shape():
    champ_game = ChampGame.model_validate(load("champ_zip.json")["Value"]["G"][0])
    game = adapt_game(champ_game, None)
    response = build_response(1252965, "Mortal Kombat X", [game])
    assert response.liga.id == 1252965 and response.gamesCount == 1 and response.games[0].id == game.id
