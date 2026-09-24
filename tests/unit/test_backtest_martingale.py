"""Tests de la logique de calcul des deux backtests de martingale (scripts/backtest_martingale_*.py)."""
from __future__ import annotations

import pytest

import backtest_martingale_mk as mk
import backtest_martingale_tt as tt


def _rnd(winner, o1=1.5, o2=2.6, round_no=1):
    return {"round": round_no, "winner": winner, "o1": o1, "o2": o2}


def test_mk_win_at_first_round_earns_stake_times_odds_minus_one():
    st = mk.new_stats()
    assert mk.run_cycle(st, [_rnd(1)], "favori") == 1
    assert st["net"] == pytest.approx(500)
    assert st["won_at"] == [1, 0, 0, 0]


def test_mk_four_losses_cost_15000_and_count_as_busted():
    st = mk.new_stats()
    mk.run_cycle(st, [_rnd(2, round_no=i) for i in range(1, 5)], "favori")
    assert st["net"] == -15000 and st["busted"] == 1 and st["staked"] == 15000
    assert st["max_dd"] == 15000


def test_mk_winning_the_fourth_round_at_typical_odds_still_loses_money():
    """Au cœur de l'explication donnée à l'utilisateur : à 1,69 (cote médiane du favori MKX),
    doubler la mise ne suffit pas à rattraper les pertes précédentes."""
    rounds = [_rnd(2, o1=1.69, o2=2.28, round_no=i) for i in range(1, 4)] + [_rnd(1, o1=1.69, o2=2.28, round_no=4)]
    st = mk.new_stats()
    mk.run_cycle(st, rounds, "favori")
    assert st["net"] == pytest.approx(-7000 + 8000 * 0.69)  # −1 480 F malgré le gain
    assert st["won_at"] == [0, 0, 0, 1]


@pytest.mark.parametrize("rule, expected", [("favori", 1), ("outsider", 2), ("joueur1", 1)])
def test_mk_pick_side(rule, expected):
    assert mk.pick(_rnd(1, o1=1.5, o2=2.6), rule) == expected


def test_mk_strategy_a_ignores_matches_without_rounds_1_to_4():
    games = {
        (1252965, "t1", 1): [_rnd(1, round_no=1), _rnd(1, round_no=2)],  # manches 3-4 sans cote
        (1252965, "t2", 2): [_rnd(1, round_no=i) for i in range(1, 6)],
    }
    st = mk.strategy_a(games, "favori", 1252965)
    assert st["cycles"] == 1


def test_mk_strategy_b_chains_cycles_across_rounds():
    games = {(1252965, "t", 1): [_rnd(1, round_no=1), _rnd(2, round_no=2), _rnd(1, round_no=3)]}
    st = mk.strategy_b(games, "favori", 1252965)
    assert st["cycles"] == 2  # gain immédiat, puis une perte suivie d'un gain
    assert st["won_at"] == [1, 1, 0, 0]


def test_tt_set_winners_parses_the_official_score_and_skips_ties():
    assert tt.set_winners("2:1 (11:13,11:6,11:6)") == [2, 1, 1]
    assert tt.set_winners("2:0 (11:7,5:5,11:9)") == [1, 1]
    assert tt.set_winners("illisible") == []


@pytest.mark.parametrize("rule, prev, expected", [
    ("joueur1", 2, 1), ("suivre", 2, 2), ("contre", 2, 1), ("suivre", None, 1),
])
def test_tt_choose(rule, prev, expected):
    assert tt.choose(rule, prev) == expected


def test_tt_continuous_martingale_spans_matches_and_busts_after_four_lost_sets():
    matches = [[2, 2], [2, 2]]  # le joueur 1 perd 4 sets d'affilée, sur deux matchs
    st = tt.simulate(matches, "joueur1", 1.85, reset_each_match=False)
    assert st["busted"] == 1 and st["net"] == -15000


def test_tt_reset_each_match_interrupts_the_cycle_without_a_bust():
    matches = [[2, 2], [2, 2]]
    st = tt.simulate(matches, "joueur1", 1.85, reset_each_match=True)
    assert st["busted"] == 0
    assert st["net"] == -6000  # deux cycles interrompus à −3 000 F chacun
    assert st["cycles"] == 2


def test_tt_each_recovered_cycle_nets_the_base_stake_at_even_odds():
    matches = [[2, 1]] * 10  # perte puis gain, cote 2,00 : +1 000 F par cycle
    st = tt.simulate(matches, "joueur1", 2.0, reset_each_match=False)
    assert st["net"] == pytest.approx(10 * 1000)


def test_tt_facts_measures_player1_rate_and_repetition():
    n_m, n_s, p1, same = tt.facts([[1, 1], [2, 1, 1]])
    assert (n_m, n_s) == (2, 5)
    assert p1 == pytest.approx(4 / 5)
    assert same == pytest.approx(2 / 3)
