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
    x = live.round_frame(feat, meta, rounds, 2)
    assert x.loc[0, "s2_before"] == 1 and x.loc[0, "prev_finish"] == "F"
    pred = live.predict_round(models, MK3, x)
    assert pred["p1"] == pytest.approx(0.7)
    assert pred["finish"] == "F" and pred["p_finish"] == pytest.approx(0.6)
    assert pred["P_finish"] == pytest.approx([0.1, 0.6, 0.1, 0.05, 0.05, 0.05, 0.05])


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


def _dq(sec, param, t, odds, game=1):
    return {"game_id": game, "g": 1074, "t": t, "param": param, "odds": odds, "blocked": False,
            "ts_server": T0 + pd.Timedelta(seconds=sec)}


def test_duration_market_only_while_the_market_is_open():
    rows = [_dq(0, 300.2050, 2170, 1.95), _dq(0, 300.2050, 2171, 1.85),
            _dq(0, 300.2350, 2170, 3.8), _dq(0, 300.2350, 2171, 1.25)]
    dm = live.duration_market(pd.DataFrame(rows), 1, 3)
    assert dm["line"] == 20.5 and dm["p_over"] == pytest.approx((1 / 1.95) / (1 / 1.95 + 1 / 1.85))
    closed = rows + [_dq(30, 300.2050, 2170, None), _dq(30, 300.2050, 2171, None)]
    assert live.duration_market(pd.DataFrame(closed), 1, 3) == {}  # manche commencée : trop tard
    assert live.duration_market(pd.DataFrame(rows), 1, 4) == {}
    assert live.duration_market(pd.DataFrame(rows).iloc[:0], 1, 3) == {}


def test_render_message_with_duration():
    match = {"league": MKX, "names": ["A", "B"], "start": T0.isoformat(), "rounds": {
        "1": {"p1": 0.6, "mkt_p1": 0.55, "dur_line": 20.5, "p_over": 0.58, "mkt_over": 0.52, "winner": 1, "seconds_real": 25.0},
        "2": {"p1": 0.6, "mkt_p1": 0.55, "dur_line": 20.5, "p_over": 0.40, "mkt_over": 0.48, "winner": 2},
    }}
    text = live.render_message(match)
    assert "durée : plus de 20,5 s 58 % (marché 52 %) → A ✅ · 25 s ✅" in text
    assert "durée : moins de 20,5 s 60 % (marché 52 %) → B ❌ · durée ⏳" in text
    assert "Bilan : vainqueur 1/2 ✅ · durée 1/1 ✅" in text


class FlakySender:
    def __init__(self, edit_ok=False):
        self.edit_ok, self.sent, self.edits = edit_ok, [], 0

    async def send(self, chat_id, text):
        self.sent.append(text)
        return 500 + len(self.sent)

    async def edit(self, chat_id, message_id, text):
        self.edits += 1
        return self.edit_ok


async def test_failed_edits_are_retried_before_any_new_message(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "RETRY_AFTER_FAILURE_SECONDS", 0.0)
    sender = FlakySender(edit_ok=False)
    pred = live.LivePredictor("dsn", tmp_path, sender, "-1")
    pred.state = {"7": {"league": MKX, "names": ["A", "B"], "start": T0.isoformat(), "done": False, "dirty": True,
                        "message_id": 42, "rounds": {"1": {"p1": 0.6}}}}
    for _ in range(live.MAX_EDIT_FAILURES):
        pred.state["7"]["dirty"] = True
        assert await pred._publish() == 0
    assert sender.sent == [] and sender.edits == live.MAX_EDIT_FAILURES  # aucun doublon sur coupure passagère
    pred.state["7"]["dirty"] = True
    assert await pred._publish() == 1  # après plusieurs échecs : nouveau message (l'ancien est perdu)
    assert len(sender.sent) == 1 and pred.state["7"]["message_id"] == 501


async def test_daily_bilan_is_published_once_after_midnight(tmp_path):
    from forecasting import journal
    sender = FlakySender()
    pred = live.LivePredictor("dsn", tmp_path, sender, "-1")
    day = pd.Timestamp("2026-09-30", tz="UTC")
    assert await pred._daily_bilan(now=day + pd.Timedelta(hours=1)) is False  # premier démarrage : repère posé
    journal.append(pred.log_file, {"ts": (day + pd.Timedelta(hours=12)).isoformat(), "league": MKX, "game_id": 1,
                                   "round": 1, "p1": 0.6, "mkt_p1": 0.5, "winner": 1})
    next_day = day + pd.Timedelta(days=1)
    assert await pred._daily_bilan(now=next_day + pd.Timedelta(minutes=2)) is False  # trop tôt après minuit
    assert await pred._daily_bilan(now=next_day + pd.Timedelta(minutes=10)) is True
    assert sender.sent[0].startswith("📈 BILAN DES PRÉDICTIONS — 30/09/2026")
    assert await pred._daily_bilan(now=next_day + pd.Timedelta(minutes=20)) is False  # une seule fois
    assert len(sender.sent) == 1
