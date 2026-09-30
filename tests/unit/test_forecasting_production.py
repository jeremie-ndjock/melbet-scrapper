"""Réentraînement de production et examen de passage champion/challenger (données synthétiques)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from forecasting import production
from forecasting.pipelines import Config
from forecasting.targets import MK3, MKX
from tests.unit.test_forecasting_pipelines import AS_OF, synth_league

CFG = Config(lag=pd.Timedelta(minutes=18), valid_days=1, embargo=pd.Timedelta(minutes=30), n_boot=50,
             min_test_rows=30, use_lgbm=False)
NOW = pd.Timestamp("2026-09-28 04:00", tz="UTC")


class SkewedModel:
    """Modèle délibérément mauvais (toujours très sûr de la classe 0) : ne doit jamais être promu."""

    def __init__(self, n_classes):
        self.classes_ = np.arange(n_classes)
        self.n = n_classes

    def predict_proba(self, X):
        p = np.full((len(X), self.n), 0.01 / (self.n - 1))
        p[:, 0] = 0.99
        return p


@pytest.fixture(scope="module")
def candidates():
    data = {MKX: synth_league(MKX, 1), MK3: synth_league(MK3, 2)}
    return production.train_production(data, AS_OF, CFG)


def test_one_challenger_per_target_with_an_unseen_exam_day(candidates):
    keys = {c["key"] for c in candidates}
    assert keys == {f"vainqueur_{MKX}", f"vainqueur_{MK3}", f"finish_{MK3}", f"duree_{MKX}"}
    for c in candidates:
        assert c["n_train"] > 0 and len(c["y_holdout"]) > 0
        assert list(c["X_holdout"].columns) == c["obj"]["variables"]


def test_promotion_rules(tmp_path, candidates):
    first = production.promote(tmp_path, "run1", candidates, now=NOW)
    assert all(d["promu"] and d["raison"] == "premier modèle de production" for d in first)
    sig, models = production.load_champions(tmp_path)
    assert set(models) == {c["key"] for c in candidates} and '"run1"' in sig
    assert all(m["version"] == "run1" for m in models.values())  # affichée dans les messages (« Modèle du JJ/MM »)

    bad = [{**c, "obj": {**c["obj"], "modele": SkewedModel(c["n_classes"])}} for c in candidates]
    second = production.promote(tmp_path, "run2", bad, now=NOW)
    assert not any(d["promu"] for d in second)
    assert all("champion conservé" in d["raison"] and d["log_loss_challenger"] > d["log_loss_champion"] for d in second)
    champions = json.loads((tmp_path / production.CHAMPION_FILE).read_text(encoding="utf-8"))
    assert {v["run"] for v in champions.values()} == {"run1"}
    assert (tmp_path / production.PRODUCTION_DIR / "run2").exists()  # challenger conservé pour traçabilité

    third = production.promote(tmp_path, "run3", candidates, now=NOW)
    assert all(d["promu"] for d in third)  # aussi bon que le champion : promu

    c = candidates[0]
    short = [{**c, "X_holdout": c["X_holdout"].iloc[:5], "y_holdout": c["y_holdout"][:5]}]
    fourth = production.promote(tmp_path, "run4", short, now=NOW)
    assert fourth[0]["promu"] is False and "trop court" in fourth[0]["raison"]


def test_unreadable_champion_is_replaced(tmp_path, candidates):
    c = candidates[0]
    (tmp_path / production.CHAMPION_FILE).write_text(json.dumps({c["key"]: {"run": "disparu"}}), encoding="utf-8")
    dec = production.promote(tmp_path, "run1", [c], now=NOW)
    assert dec[0]["promu"] and dec[0]["raison"] == "champion illisible, remplacé"
    assert production.read_champions(tmp_path / "absent") == {}
