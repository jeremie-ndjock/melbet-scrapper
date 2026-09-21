"""Tests du décodage des paramètres et de l'aplatissement des cotes (sans réseau ni base)."""
from __future__ import annotations

from decimal import Decimal

from collector.normalize import decode_extra, flatten_game
from collector.sources.v3.models import Game


def test_decode_extra_no_params_is_none():
    assert decode_extra(1, None) == (None, None)  # groupe 1x2 : aucun paramètre
    assert decode_extra(1, []) == (None, None)


def test_decode_extra_single_round_value():
    # Victoire dans le Round : un seul paramètre, un numéro de round.
    assert decode_extra(1050, ["(6)"]) == (6, None)


def test_decode_extra_single_line_value():
    # Total : un seul paramètre, une ligne.
    round_no, line = decode_extra(17, ["(7.5)"])
    assert round_no is None
    assert line == Decimal("7.5")


def test_decode_extra_round_and_line():
    # Durée du Round : round puis ligne.
    round_no, line = decode_extra(1074, ["6", "17.5"])
    assert round_no == 6
    assert line == Decimal("17.5")


def test_decode_extra_unknown_group_defaults_to_line():
    # Un groupe non répertorié ne doit jamais faire échouer le décodage : traité comme une ligne.
    round_no, line = decode_extra(999999, ["(3)"])
    assert round_no is None
    assert line == Decimal("3")


def test_decode_extra_unparsable_value_is_ignored():
    assert decode_extra(17, ["abc"]) == (None, None)


def _game(**overrides):
    base = {
        "id": 1, "startTs": 1000, "updateTs": 1005,
        "opponent1": {"fullName": "A", "opps": [{"id": 10}]},
        "opponent2": {"fullName": "B", "opps": [{"id": 20}]},
        "scores": {"fullScore": "0-0", "fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0}},
        "eventGroups": [],
    }
    base.update(overrides)
    return Game.model_validate(base)


def test_flatten_game_builds_selection_identity():
    game = _game(eventGroups=[
        {"groupId": 1, "events": [[{"type": 1, "cf": 1.5}], [{"type": 3, "cf": 2.5}]]},
        {"groupId": 17, "events": [[{"type": 9, "parameter": 7.5, "cf": 1.4, "isCenter": True,
                                      "eventParams": {"params": ["(7.5)"]}}]]},
    ])
    rows = flatten_game(game)
    assert set(rows) == {(1, 1, Decimal(0)), (1, 3, Decimal(0)), (17, 9, Decimal("7.5"))}
    total_row = rows[(17, 9, Decimal("7.5"))]
    assert total_row.odds == Decimal("1.4") and total_row.is_center and total_row.line == Decimal("7.5")


def test_flatten_game_blocked_selection_is_preserved():
    game = _game(eventGroups=[{"groupId": 1, "events": [[{"type": 1, "cf": 1.1, "blocked": True}]]}])
    rows = flatten_game(game)
    assert rows[(1, 1, Decimal(0))].blocked is True


def test_flatten_game_missing_parameter_uses_zero_default():
    game = _game(eventGroups=[{"groupId": 1, "events": [[{"type": 1, "cf": 1.1}]]}])
    row = flatten_game(game)[(1, 1, Decimal(0))]
    assert row.param == Decimal(0)  # convention du schéma : « sans paramètre » = 0, jamais NULL
