"""Parcours complet des trois évaluations sur des matchs synthétiques, puis la ligne de
commande (extraction simulée) : rapport, registre des essais, modèles sauvegardés,
reproductibilité."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

import forecasting.__main__ as cli
from forecasting import quality
from forecasting.pipelines import Config, label_check, run_all
from forecasting.report import AUCUN, AVANTAGE, NON_EVALUABLE, PROMETTEUR, _round
from forecasting.targets import FINISH_CLASSES, FINISH_MARKET_T_ORDERED, MK3, MKX
from forecasting.trials import TrialRegistry

START = pd.Timestamp("2026-09-20 00:00", tz="UTC")
DAYS, ODDS_DAYS = pd.Timedelta(days=4), pd.Timedelta(days=1.5)
AS_OF = START + DAYS
STRENGTH = {"A": 1.0, "B": 0.5, "C": 0.0, "D": -0.5, "E": -1.0, "F": 0.2}
IDS = {name: i + 1 for i, name in enumerate(STRENGTH)}
CLASS_P = np.array([0.48, 0.38, 0.08, 0.025, 0.015, 0.008, 0.012])
CLASS_P = CLASS_P / CLASS_P.sum()
LINES = (24.5, 30.5, 36.5)


def _odds_rows(game, g, t, param, price, ts, close_ts):
    return [{"game_id": game, "g": g, "t": t, "param": param, "odds": price, "blocked": False, "ts_server": ts},
            {"game_id": game, "g": g, "t": t, "param": param, "odds": np.nan, "blocked": False, "ts_server": close_ts}]


def synth_league(league: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    matches, rounds, odds, ends = [], [], [], []
    game, t = league * 100000, START
    w_start = START + DAYS - ODDS_DAYS
    while t < START + DAYS:
        a, b = rng.choice(list(STRENGTH), 2, replace=False)
        p = 1 / (1 + math.exp(-(STRENGTH[a] - STRENGTH[b])))
        s1 = s2 = rn = 0
        live = t >= w_start
        while s1 < 5 and s2 < 5:
            rn += 1
            w = 1 if rng.uniform() < p else 2
            s1, s2 = s1 + (w == 1), s2 + (w == 2)
            fin = FINISH_CLASSES[rng.choice(7, p=CLASS_P)]
            sec = float(max(10, round(rng.normal(30, 6))))
            r_start = t + pd.Timedelta(seconds=90 * (rn - 1))
            rounds.append({"game_id": game, "round_no": rn, "winner": w, "seconds": sec if live else np.nan,
                           "finish_code": fin, "finish_di": None, "fw": None})
            if live:
                ends.append({"game_id": game, "round_no": rn, "round_end": r_start + pd.Timedelta(seconds=60)})
                quote_ts = r_start - pd.Timedelta(seconds=30)
                pf = float(np.clip(p + rng.normal(0, 0.03), 0.05, 0.95))
                odds += _odds_rows(game, 1050, 2140, rn, 1 / (pf * 1.05), quote_ts, r_start)
                odds += _odds_rows(game, 1050, 2141, rn, 1 / ((1 - pf) * 1.05), quote_ts, r_start)
                if league == MK3:
                    for tt, cp in zip(FINISH_MARKET_T_ORDERED, CLASS_P):
                        odds += _odds_rows(game, 1066, tt, rn, 1 / (cp * 1.08), quote_ts, r_start)
                if league == MKX:
                    for line in LINES:
                        po = 1 - 0.5 * (1 + math.erf((line - 30) / (6 * math.sqrt(2))))
                        po = min(max(po, 0.03), 0.97)
                        param = rn * 100 + line / 100
                        odds += _odds_rows(game, 1074, 2170, param, 1 / (po * 1.05), quote_ts, r_start)
                        odds += _odds_rows(game, 1074, 2171, param, 1 / ((1 - po) * 1.05), quote_ts, r_start)
        matches.append({"game_id": game, "league_id": league, "p1_id": IDS[a], "p2_id": IDS[b], "p1_name": a,
                        "p2_name": b, "final_score1": s1, "final_score2": s2, "winner": 1 if s1 == 5 else 2,
                        "date_start": t})
        game += 1
        t += pd.Timedelta(minutes=15)
    m, r, _ = quality.clean(pd.DataFrame(matches), pd.DataFrame(rounds))
    return {"matches": m, "rounds": r, "odds": pd.DataFrame(odds), "round_ends": pd.DataFrame(ends), "w_start": w_start}


@pytest.fixture(scope="module")
def data():
    return {MKX: synth_league(MKX, 1), MK3: synth_league(MK3, 2)}


@pytest.fixture(autouse=True)
def _never_send_real_telegram(monkeypatch):
    """Le conteneur de tests charge le vrai .env : aucun test ne doit pouvoir écrire sur un vrai
    salon Telegram."""
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_PREDICTION_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)


CFG = Config(lag=pd.Timedelta(minutes=18), valid_days=1, embargo=pd.Timedelta(minutes=30), n_boot=100,
             walk_folds=2, walk_step=pd.Timedelta(hours=12), min_test_rows=30)


def test_run_all_evaluates_the_three_targets(data, tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    meta = {"as_of": AS_OF.isoformat(), "git_commit": "test"}
    store: dict = {}
    results = run_all(data, as_of=AS_OF, cfg=CFG, registry=registry, meta=meta,
                      targets={"winner", "finish", "duration"}, store=store)
    by_target = {(r["cible"], r["ligue"]): r for r in results}
    assert set(by_target) == {("vainqueur", MKX), ("duree", MKX), ("vainqueur", MK3), ("finish", MK3)}
    for res in results:
        assert res["verdict"] in {AVANTAGE, PROMETTEUR, AUCUN, NON_EVALUABLE}, res.get("erreur")
        assert res["n_test"] > 0 and "n_paris" in res["backtest"]
        assert all(math.isfinite(s["log_loss"]) for s in res["scores"].values())
    assert by_target[("duree", MKX)]["preliminaire"] is True
    assert by_target[("vainqueur", MKX)]["validation_glissante"], "la validation glissante doit produire des semaines"
    assert "Cotes écartées car postérieures à la manche : 0" in by_target[("vainqueur", MK3)]["notes"]
    assert set(store) == {f"vainqueur_{MKX}", f"vainqueur_{MK3}", f"finish_{MK3}"}
    assert len((tmp_path / "trials.jsonl").read_text(encoding="utf-8").splitlines()) == 4

    # Même date de référence, même code : résultats identiques et aucun essai recompté.
    again = run_all(data, as_of=AS_OF, cfg=CFG, registry=registry, meta=meta, targets={"winner", "finish", "duration"})
    assert json.dumps(_round(again), sort_keys=True, default=str) == json.dumps(_round(results), sort_keys=True, default=str)
    assert len((tmp_path / "trials.jsonl").read_text(encoding="utf-8").splitlines()) == 4


def test_not_enough_market_rows_is_reported_as_not_evaluable(data, tmp_path):
    thin = {MKX: {**data[MKX], "odds": data[MKX]["odds"].iloc[:0]}}
    results = run_all(thin, as_of=AS_OF, cfg=CFG, registry=TrialRegistry(tmp_path / "t.jsonl"),
                      meta={"as_of": "x", "git_commit": "y"}, targets={"winner", "duration"})
    assert [r["verdict"] for r in results] == [NON_EVALUABLE, NON_EVALUABLE]
    assert all("trop peu" in r["erreur"] for r in results)


def test_label_check_flags_labels_inconsistent_with_the_market():
    P = np.tile([0.5, 0.5], (2000, 1))
    assert label_check(np.r_[np.zeros(1000), np.ones(1000)].astype(int), P)["ok"]
    assert not label_check(np.r_[np.zeros(1800), np.ones(200)].astype(int), P)["ok"]


def test_cli_run_and_check_data(data, tmp_path, monkeypatch, capsys):
    async def fake_gather(dsn, as_of, with_odds=True):
        return data, {"delai_disponibilite": {"p99_duree_match_s": 1068.0}}, pd.Timedelta(minutes=18)

    monkeypatch.setattr(cli, "gather", fake_gather)
    monkeypatch.setattr(cli, "Config", lambda **kw: Config(**{**CFG.__dict__, **kw}))
    monkeypatch.setenv("DATABASE_URL", "postgresql://inutilise")
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    assert cli.main(["check-data", "--as-of", AS_OF.isoformat()]) == 0
    assert '"lag_minutes": 18.0' in capsys.readouterr().out
    sent = []

    async def fake_send(run, sender=None):
        sent.append(run["as_of"])
        return True

    monkeypatch.setattr(cli.notify, "send_summary", fake_send)
    assert cli.main(["run", "--as-of", AS_OF.isoformat(), "--target", "winner", "--no-lgbm",
                     "--reports", str(tmp_path / "rep"), "--artifacts", str(tmp_path / "art")]) == 0
    assert sent == [AS_OF.isoformat()]
    assert cli.main(["run", "--as-of", AS_OF.isoformat(), "--target", "winner", "--no-lgbm", "--no-telegram",
                     "--reports", str(tmp_path / "rep"), "--artifacts", str(tmp_path / "art")]) == 0
    assert len(sent) == 1
    run_dir = next((tmp_path / "rep").glob("*_abc123"))
    assert (run_dir / "rapport.md").exists() and (run_dir / "resultats.json").exists()
    assert len(list((tmp_path / "art").glob("*/*.joblib"))) == 2


def test_default_as_of_is_three_hours_back_rounded_to_the_hour():
    assert cli.default_as_of(pd.Timestamp("2026-09-25 16:47", tz="UTC")) == pd.Timestamp("2026-09-25 13:00", tz="UTC")
