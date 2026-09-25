"""Tests des cotes (décodage, clôture, marge, ligne principale), des métriques et du backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import log_loss as sk_log_loss

from forecasting import backtest as bt
from forecasting.market import closing_quotes, decode_param, devig, devig_power, main_line
from forecasting.metrics import brier, calibration_table, classwise_ece, log_loss, n_required, paired_bootstrap

T0 = pd.Timestamp("2026-09-24 12:00", tz="UTC")


def _q(sec, game, g, t, param, odds, blocked=False):
    return {"game_id": game, "g": g, "t": t, "param": param, "odds": odds, "blocked": blocked,
            "ts_server": T0 + pd.Timedelta(seconds=sec)}


def test_decode_param_duration_and_round_markets():
    g = pd.Series([1074, 1074, 1050, 1066])
    p = pd.Series([100.2050, 900.4850, 3.0, 9.0])
    rnd, line = decode_param(g, p)
    assert rnd.tolist() == [1, 9, 3, 9]
    assert line.tolist() == [20.5, 48.5, -1.0, -1.0]


def test_closing_quotes_keeps_last_instant_where_both_sides_coexist():
    odds = pd.DataFrame([
        _q(0, 1, 1050, 2140, 2.0, 1.50), _q(0, 1, 1050, 2141, 2.0, 2.60),
        _q(10, 1, 1050, 2140, 2.0, 1.45),                       # seul P1 bouge : P2 reste à 2,60
        _q(20, 1, 1050, 2141, 2.0, np.nan),                     # P2 retiré
        _q(30, 1, 1050, 2140, 2.0, 1.20),                       # P1 bouge encore : plus jouable en paire
    ])
    q = closing_quotes(odds, {1050: (2141, 2140)})
    row = q.iloc[0]
    assert (row["close_2140"], row["close_2141"]) == (1.45, 2.60)
    assert (row["open_2140"], row["open_2141"]) == (1.50, 2.60)
    assert row["ts_close"] == T0 + pd.Timedelta(seconds=10)
    assert row["round_no"] == 2


def test_closing_quotes_ignores_blocked_quotes_and_markets_that_never_coexist():
    odds = pd.DataFrame([
        _q(0, 1, 1050, 2140, 1.0, 1.50), _q(0, 1, 1050, 2141, 1.0, 2.60),
        _q(5, 1, 1050, 2140, 1.0, 1.40, blocked=True),          # suspendue : l'état jouable reste celui de t=0
        _q(0, 2, 1050, 2140, 1.0, 1.80),                        # match 2 : P2 jamais proposé
    ])
    q = closing_quotes(odds, {1050: (2141, 2140)})
    assert q["game_id"].tolist() == [1]
    assert q.iloc[0]["close_2140"] == 1.50


def test_devig_removes_the_margin():
    P, margin = devig(np.array([[1.9, 1.9], [1.5, 2.6]]))
    assert P[0].tolist() == pytest.approx([0.5, 0.5])
    assert margin[0] == pytest.approx(2 / 1.9 - 1)
    assert P.sum(axis=1) == pytest.approx([1.0, 1.0])
    power = devig_power(np.array([[1.5, 2.6, 30.0]]))
    assert power.sum() == pytest.approx(1.0)


def test_main_line_picks_the_most_balanced_line():
    quotes = pd.DataFrame({
        "game_id": [1, 1, 1], "round_no": [1, 1, 1], "line": [17.5, 20.5, 23.5],
        "close_2170": [1.26, 1.995, 3.79], "close_2171": [3.79, 1.905, 1.26],
    })
    assert main_line(quotes, 2170, 2171)["line"].tolist() == [20.5]


def test_log_loss_and_brier_match_the_reference():
    y = np.array([1, 0, 1, 1])
    p = np.array([0.8, 0.3, 0.6, 0.9])
    assert log_loss(y, p) == pytest.approx(sk_log_loss(y, p))
    P = np.array([[0.7, 0.2, 0.1], [0.1, 0.8, 0.1]])
    assert log_loss([0, 2], P) == pytest.approx(sk_log_loss([0, 2], P, labels=[0, 1, 2]))
    assert brier([0], np.array([[1.0, 0.0]])) == 0.0


def test_classwise_ece_hand_computed_case_and_calibrated_data():
    p = np.array([0.2, 0.2, 0.8, 0.8])
    y = np.array([0, 1, 1, 1])
    res = classwise_ece(p, y, n_bins=2, strategy="uniform")
    assert res["ece"] == pytest.approx(0.5 * abs(0.2 - 0.5) + 0.5 * abs(0.8 - 1.0))
    assert res["nonempty_frac"] == 1.0
    rng = np.random.default_rng(0)
    pc = rng.uniform(0.3, 0.7, 50000)
    yc = (rng.uniform(size=pc.size) < pc).astype(int)
    assert classwise_ece(pc, yc)["ece"] < 0.01
    assert classwise_ece(pc, yc, strategy="uniform")["nonempty_frac"] < 0.8  # la règle de Bath échoue


def test_calibration_table_and_bootstrap():
    rows = calibration_table(np.linspace(0.1, 0.9, 100), np.tile([0, 1], 50), n_bins=4)
    assert sum(r["n"] for r in rows) == 100
    res = paired_bootstrap(np.ones(100), np.zeros(100), np.repeat(np.arange(20), 5), n_boot=200)
    assert res["moyenne"] == 1.0 and res["ic_bas"] == pytest.approx(1.0)
    assert n_required(0.03, 0.83) == pytest.approx(2941, abs=1)


def test_value_bets_arithmetic_and_single_bet_per_round():
    P_model = np.array([[0.30, 0.70], [0.50, 0.50]])
    P_fair = np.array([[0.40, 0.60], [0.50, 0.50]])
    odds = np.array([[2.4, 1.6], [1.9, 1.9]])
    bets = bt.value_bets(P_model, P_fair, odds, y=np.array([1, 0]), tau=0.05)
    assert bets["rows"].tolist() == [0] and bets["choice"].tolist() == [1]
    assert bets["profit"].tolist() == pytest.approx([600.0])
    s = bt.summarize(bets, clusters=np.array([1, 2]), n_boot=50)
    assert s["n_paris"] == 1 and s["resultat"] == pytest.approx(600.0) and s["echantillon_insuffisant"]


def test_value_bets_multiclass_picks_highest_expected_value():
    P_model = np.array([[0.40, 0.30, 0.30]])
    P_fair = np.array([[0.45, 0.25, 0.30]])
    odds = np.array([[2.1, 3.8, 3.2]])
    bets = bt.value_bets(P_model, P_fair, odds, y=np.array([2]), tau=0.0)
    assert bets["choice"].tolist() == [1] and bets["profit"].tolist() == [-1000.0]


def test_choose_tau_falls_back_when_too_few_bets():
    P = np.array([[0.5, 0.5]])
    assert bt.choose_tau(P, P, np.array([[1.9, 1.9]]), np.array([0]), min_bets=5, default=0.02) == 0.02


def test_summarize_without_bets():
    empty = bt.value_bets(np.array([[0.5, 0.5]]), np.array([[0.5, 0.5]]), np.array([[1.9, 1.9]]), np.array([0]), 0.1)
    assert bt.summarize(empty, np.array([1]))["n_paris"] == 0


def test_backtest_breakdown_by_outcome():
    bets = {"choice": np.array([0, 2, 2]), "profit": np.array([-1000.0, 5000.0, -1000.0]),
            "odds": np.array([2.0, 6.0, 8.0])}
    out = bt.by_outcome(bets, ["R", "F", "Hk"])
    assert set(out) == {"R", "Hk"}
    assert out["Hk"] == {"n_paris": 2, "resultat": 4000.0, "rendement": 2.0, "cote_moyenne": 7.0}
