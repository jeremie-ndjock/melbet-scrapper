"""Prédictions en direct : rendu du message, probabilités du marché, prédiction d'une manche."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting import live
from forecasting.features import FIGHTER_CATS, ROUND_STATE, WINNER_NUM
from forecasting.targets import FINISH_MARKET_T_ORDERED, MK3, MKX

T0 = pd.Timestamp("2026-09-25 19:00", tz="UTC")


class ConstModel:
    """Modèle factice : probabilités fixes, pour tester la mécanique sans entraînement."""

    def __init__(self, probs):
        self.probs = np.asarray(probs, float)
        self.classes_ = np.arange(len(probs))

    def predict_proba(self, X):
        return np.tile(self.probs, (len(X), 1))


class Identity:
    def transform(self, p):
        return p


def test_render_message_shows_predictions_results_and_disclaimer():
    match = {"league": MK3, "names": ["Scorpion", "Sub-Zero"], "start": T0.isoformat(), "done": True, "rounds": {
        "1": {"p1": 0.62, "mkt_p1": 0.58, "finish": "R", "p_finish": 0.47, "mkt_finish": 0.45, "winner": 1, "finish_real": "F"},
        "2": {"p1": 0.40, "mkt_p1": None, "finish": "F", "p_finish": 0.41, "winner": 2, "finish_real": "F"},
        "3": {"p1": 0.55, "finish": "R", "p_finish": 0.5},
    }}
    text = live.render_message(match)
    assert text.startswith("🔮 PRÉDICTIONS — MORTAL KOMBAT 3\n🥊 Scorpion VS Sub-Zero · début 19:00 UTC")
    assert "M1 · Scorpion 62 % (marché 58 %) · finish : Regular 47 % (marché 45 %) → Scorpion ✅ · Fatality ❌" in text
    assert "M2 · Sub-Zero 60 % (marché —)" in text and "→ Sub-Zero ✅ · Fatality ✅" in text
    assert "M3 · Scorpion 55 %" in text and text.count("⏳") == 1
    assert "Bilan : vainqueur 2/2 ✅ · finish 1/2 ✅" in text and "🏁 Match terminé" in text
    assert text.endswith(live.DISCLAIMER)


def test_render_message_without_finish_market():
    text = live.render_message({"league": MKX, "names": ["A", "B"], "start": T0.isoformat(),
                                "rounds": {"1": {"p1": 0.3, "winner": 1}}})
    assert "M1 · B 70 % (marché —) → A ❌" in text and "finish" not in text.split("\n")[3]


def _q(game, g, t, param, odds, sec=0):
    return {"game_id": game, "g": g, "t": t, "param": param, "odds": odds, "blocked": False,
            "ts_server": T0 + pd.Timedelta(seconds=sec)}


def test_market_probs_for_winner_and_finish():
    rows = [_q(1, 1050, 2140, 2.0, 1.5), _q(1, 1050, 2141, 2.0, 2.6)]
    rows += [_q(1, 1066, t, 2.0, o) for t, o in zip(FINISH_MARKET_T_ORDERED, (2.0, 2.5, 11.0, 38.0, 42.0, 60.0, 90.0))]
    mk = live.market_probs(pd.DataFrame(rows), 1, 2)
    assert mk["p1"] == pytest.approx((1 / 1.5) / (1 / 1.5 + 1 / 2.6))
    assert sum(mk["finish"].values()) == pytest.approx(1.0) and mk["finish"]["R"] > mk["finish"]["F"]
    assert live.market_probs(pd.DataFrame(rows), 1, 3) == {}
    assert live.market_probs(pd.DataFrame(rows), 2, 2) == {}


def test_predict_round_uses_both_models():
    models = {f"vainqueur_{MK3}": {"modele": ConstModel([0.3, 0.7]), "calibrateur": Identity(),
                                   "variables": WINNER_NUM + FIGHTER_CATS},
              f"finish_{MK3}": {"modele": ConstModel([0.1, 0.6, 0.1, 0.05, 0.05, 0.05, 0.05]), "calibrateur": _Temp(),
                                "variables": ["round_no", "prev_finish"]}}
    feat = pd.Series({c: 0.5 for c in WINNER_NUM if c not in ROUND_STATE})
    meta = {"game_id": 7, "p1_key": "1", "p2_key": "2"}
    rounds = pd.DataFrame({"round_no": [1], "winner": [2], "seconds": [np.nan], "finish": ["F"]})
    pred = live.predict_round(models, MK3, feat, meta, rounds, 2)
    assert pred["p1"] == pytest.approx(0.7)
    assert pred["finish"] == "F" and pred["p_finish"] == pytest.approx(0.6)


class _Temp:
    def transform(self, P):
        return P


def test_latest_models_picks_the_most_recent_run(tmp_path):
    import joblib
    for run in ("20260924T1500Z_a", "20260925T1500Z_b"):
        (tmp_path / run).mkdir()
        joblib.dump({"nom": run}, tmp_path / run / f"vainqueur_{MKX}.joblib")
    (tmp_path / "20260926T1500Z_vide").mkdir()
    name, models = live.latest_models(tmp_path)
    assert name == "20260925T1500Z_b" and models[f"vainqueur_{MKX}"]["nom"] == name
    assert live.latest_models(tmp_path / "absent") == (None, {})
