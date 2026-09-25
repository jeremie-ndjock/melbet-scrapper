"""Tests des variables (absence de fuite d'information), des découpages, de la qualité des
données, des modèles, du registre des essais et du verdict."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting import quality
from forecasting.features import asof_cumulative, intra_match, match_features
from forecasting.models import LGBMModel, LogLinearPool, PlattCalibrator, TemperatureCalibrator, make_logreg
from forecasting.report import AUCUN, AVANTAGE, NON_EVALUABLE, PROMETTEUR, render_markdown, verdict, write_report
from forecasting.split import before, between, match_blocks, split_by_match_order, weekly_folds
from forecasting.targets import duration_label, finish_label
from forecasting.trials import TrialRegistry

T0 = pd.Timestamp("2026-09-01 00:00", tz="UTC")
LAG = pd.Timedelta(minutes=18)


def _matches(specs):
    """specs : (game_id, minutes après T0, p1, p2, vainqueur)."""
    return pd.DataFrame([{
        "game_id": g, "league_id": 1, "date_start": T0 + pd.Timedelta(minutes=m), "p1_key": a, "p2_key": b,
        "p1_id": None, "p2_id": None, "p1_name": a, "p2_name": b, "winner": w,
        "final_score1": 5 if w == 1 else 2, "final_score2": 2 if w == 1 else 5,
    } for g, m, a, b, w in specs])


def _rounds_for(matches, finish="F"):
    rows = []
    for _, m in matches.iterrows():
        seq = [m["winner"]] * 5 + [3 - m["winner"]] * 2
        for i, w in enumerate(seq, start=1):
            rows.append({"game_id": m["game_id"], "round_no": i, "winner": w, "seconds": 30.0, "finish": finish,
                         "finish_code": finish, "finish_di": None})
    return pd.DataFrame(rows)


def test_targets():
    assert finish_label("Hk", None) == "Hk"
    assert finish_label(None, "Hara-Kiri") == "Hk"
    assert finish_label(None, None) is None
    assert duration_label(31, 30.5) == 1 and duration_label(30, 30.5) == 0 and duration_label(None, 30.5) is None


def test_intra_match_score_before_and_previous_round():
    r = pd.DataFrame({"game_id": [1, 1, 1], "round_no": [1, 2, 3], "winner": [1, 2, 1],
                      "seconds": [20.0, np.nan, 25.0], "finish": ["F", "R", "B"]})
    im = intra_match(r)
    assert im["s1_before"].tolist() == [0, 1, 1] and im["s2_before"].tolist() == [0, 0, 1]
    assert np.isnan(im.loc[0, "prev_winner_p1"]) and im.loc[1, "prev_winner_p1"] == 1.0
    assert im["prev_finish"].tolist() == ["aucun", "F", "R"]
    assert im["match_F_so_far"].tolist() == [0, 1, 1]


def test_fighter_history_never_sees_the_future():
    """Changer les issues des matchs futurs ne doit changer aucune variable passée."""
    base = _matches([(1, 0, "A", "B", 1), (2, 60, "A", "B", 1), (3, 120, "A", "B", 1)])
    feat_a = match_features(base, _rounds_for(base), lag=LAG)
    flipped = base.copy()
    flipped.loc[flipped["game_id"] == 3, "winner"] = 2
    feat_b = match_features(flipped, _rounds_for(flipped), lag=LAG)
    pd.testing.assert_frame_equal(feat_a.iloc[:3], feat_b.iloc[:3])
    assert feat_a.loc[0, "p1_rounds_n"] == 0  # premier match : aucun historique
    assert feat_a.loc[2, "p1_rounds_n"] == 14  # deux matchs de 7 manches avant le troisième


def test_history_respects_the_availability_lag_and_simultaneous_matches():
    m = _matches([(1, 0, "A", "B", 1), (2, 0, "A", "C", 1), (3, 10, "A", "B", 1), (4, 30, "A", "B", 1)])
    feat = match_features(m, _rounds_for(m), lag=LAG)
    assert feat.loc[1, "p1_rounds_n"] == 0  # match simultané : invisible
    assert feat.loc[2, "p1_rounds_n"] == 0  # commencé 10 min après : les matchs précédents ne sont pas finis
    assert feat.loc[3, "p1_rounds_n"] == 21  # 30 min après : les trois précédents sont disponibles
    assert feat.loc[3, "h2h_rounds_n"] == 14


def test_asof_cumulative_excludes_exact_equality():
    contrib = pd.DataFrame({"k": ["x", "x"], "avail_ts": [T0, T0 + pd.Timedelta(minutes=1)], "v": [1, 2]})
    q = pd.DataFrame({"k": ["x", "x", "y"], "date_start": [T0, T0 + pd.Timedelta(minutes=5), T0]})
    out = asof_cumulative(contrib, "k", q, "k", ["v"])
    assert out["v"].tolist() == [0, 3, 0]


def test_splits():
    ds = pd.Series([T0 + pd.Timedelta(hours=h) for h in range(10)])
    gid = pd.Series(range(10))
    assert before(ds, T0 + pd.Timedelta(hours=3), LAG).sum() == 3
    assert between(ds, T0 + pd.Timedelta(hours=2), T0 + pd.Timedelta(hours=5)).sum() == 3
    first, second = split_by_match_order(gid, ds, 0.3, pd.Timedelta(hours=1))
    assert first.sum() == 3 and second.sum() == 6 and not (first & second).any()
    blocks, starts = match_blocks(gid, ds, 5)
    assert blocks.tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4] and len(starts) == 5
    folds = weekly_folds(pd.Series([T0 + pd.Timedelta(days=d) for d in range(21)]), T0 + pd.Timedelta(days=7),
                         T0 + pd.Timedelta(days=21), LAG, pd.Timedelta(hours=1))
    assert len(folds) == 2 and folds[0][1].sum() == 7


def test_quality_excludes_inconsistent_and_misoriented_matches():
    m = _matches([(1, 0, "A", "B", 1), (2, 60, "A", "B", 2), (3, 120, "A", "B", 1)])
    r = _rounds_for(m)
    r.loc[(r["game_id"] == 2) & (r["round_no"] == 1), "winner"] = 1  # score des manches ≠ score final
    events = pd.DataFrame({"game_id": [3], "league_id": [1], "p1_id": [99.0], "p2_id": [7.0]})
    m["p1_id"] = [1.0, 1.0, 7.0]
    m["p2_id"] = [2.0, 2.0, 99.0]
    kept, rounds, rep = quality.clean(m, r, events)
    assert kept["game_id"].tolist() == [1]
    assert rep["exclus_score_incoherent"] == 1 and rep["exclus_orientation_inversee"] == 1
    assert set(rounds["game_id"]) == {1}
    assert kept.loc[0, "p1_key"] == "1"


def test_duration_coverage_and_match_lag():
    r = pd.DataFrame({"game_id": [1, 1, 2], "round_no": [1, 2, 1], "seconds": [20.0, np.nan, np.nan]})
    assert quality.duration_coverage(r) == {1: 1.0, 2: 0.0}
    ev = pd.DataFrame({"start_ts": [T0] * 3, "finished_at": [T0 + pd.Timedelta(minutes=m) for m in (10, 12, 25)]})
    lag, info = quality.match_lag(ev)
    assert lag > pd.Timedelta(minutes=18) and info["p99_duree_match_s"] > 1400


def test_calibrators_and_pool():
    rng = np.random.default_rng(1)
    p_true = rng.uniform(0.2, 0.8, 20000)
    y = (rng.uniform(size=p_true.size) < p_true).astype(int)
    distorted = 1 / (1 + np.exp(-2.0 * np.log(p_true / (1 - p_true))))
    fixed = PlattCalibrator().fit(distorted, y).transform(distorted)
    assert np.abs(fixed - p_true).mean() < 0.02
    P = np.column_stack([1 - distorted, distorted])
    assert TemperatureCalibrator().fit(P, y).t > 1.5
    noise = rng.uniform(0.2, 0.8, p_true.size)
    pool = LogLinearPool(binary=True).fit(p_true, noise, y)
    assert abs(pool.model_weight) < 0.1
    assert LogLinearPool(binary=True, use_model=False).fit(p_true, None, y).model_weight == 0.0
    Pm = np.column_stack([1 - p_true, p_true])
    multi = LogLinearPool(binary=False).fit(Pm, np.full_like(Pm, 0.5), y)
    assert multi.predict(Pm, np.full_like(Pm, 0.5)).sum(axis=1) == pytest.approx(np.ones(len(y)))


def test_models_fit_on_small_data():
    rng = np.random.default_rng(2)
    X = pd.DataFrame({"a": rng.normal(size=600), "k": rng.choice(["x", "y"], 600)})
    y = (X["a"] + rng.normal(scale=0.5, size=600) > 0).astype(int).to_numpy()
    lr = make_logreg(["a"], ["k"]).fit(X, y)
    assert lr.predict_proba(X).shape == (600, 2)
    lgbm = LGBMModel(["a"], ["k"], n_classes=2).fit(X, y)
    assert lgbm.predict_proba(X).shape == (600, 2) and lgbm.n_iterations >= 1
    assert list(lgbm.classes_) == [0, 1]


def test_trial_registry_counts_distinct_trials(tmp_path):
    reg = TrialRegistry(tmp_path / "trials.jsonl")
    rec = {"as_of": "a", "git_commit": "c1", "cible": "vainqueur", "ligue": 1, "variante": "base"}
    assert reg.register(rec) == 1
    assert reg.register(rec) == 1  # même essai relancé : pas recompté
    assert reg.register({**rec, "git_commit": "c2"}) == 2
    assert reg.k_for({**rec, "ligue": 2}) == 1


def test_verdict_ladder():
    good_bt = {"ic_bas": 0.01, "echantillon_insuffisant": False}
    assert verdict({"moyenne": -0.01, "ic_haut": -0.001}, good_bt, True, True) == AVANTAGE
    assert verdict({"moyenne": -0.01, "ic_haut": 0.002}, good_bt, True, True) == PROMETTEUR
    assert verdict({"moyenne": 0.01, "ic_haut": 0.02}, good_bt, True, True) == AUCUN
    assert verdict({"moyenne": -0.01, "ic_haut": -0.001}, good_bt, True, False) == NON_EVALUABLE


def test_report_rendering(tmp_path):
    scores = {"log_loss": 0.69, "brier": 0.5, "ece_quantile": 0.01, "ece_uniforme": 0.02, "intervalles_non_vides": 0.5, "n": 10}
    res = {"titre": "Test", "verdict": AUCUN, "preliminaire": True, "n_test": 10, "n_train": 5, "modele_retenu": "x",
           "poids_modele": None, "poids_modele_ic": [None, None], "scores": {"Marché": scores},
           "delta": {"moyenne": 0.001, "ic_bas": -0.01, "ic_haut": 0.01}, "delta_marche_brut": {"moyenne": 0.0, "ic_bas": 0.0, "ic_haut": 0.0},
           "k": 1, "controle_etiquettes": {"ok": True}, "tau": 0.02,
           "backtest": {"n_paris": 3, "resultat": -500.0, "rendement": -0.1, "ic_bas": -0.5, "ic_haut": 0.3,
                        "echantillon_insuffisant": True, "n_requis": 3000,
                        "par_issue": {"Hk": {"n_paris": 3, "resultat": -500.0, "rendement": -0.17, "cote_moyenne": 40.0}}},
           "variabilite_marche": [0.0, 0.001],
           "validation_glissante": [{"semaine": "2026-09-01", "n": 5, "base": 0.7, "modele": 0.69, "heure": 0.69, "series": 0.7}],
           "notes": ["une note"]}
    run = {"as_of": "2026-09-25T13:00:00+00:00", "git_commit": "abc", "lag_minutes": 18.0, "qualite": {"x": 1.23456789},
           "resultats": [res, {"titre": "Vide", "verdict": NON_EVALUABLE, "erreur": "trop peu"}]}
    md = render_markdown(run)
    assert "PRÉLIMINAIRE" in md and "il faudrait ≈ 3000 paris" in md and "Évaluation impossible" in md
    assert "| Hk | 3 | -500 F |" in md and "cote quasiment figée" in md and "| 2026-09-01 |" in md
    assert "value bets, mise fixe" in md
    out = write_report(tmp_path / "r", run)
    assert (out / "rapport.md").exists() and '"x": 1.234568' in (out / "resultats.json").read_text(encoding="utf-8")
