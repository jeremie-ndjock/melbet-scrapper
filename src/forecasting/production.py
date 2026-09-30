"""Modèles de production réentraînés chaque jour, promus seulement s'ils réussissent l'examen de
passage champion/challenger (plan d'entraînement, section 12 ; Memoire.md, section 40).

Chaque cible est entraînée sur toutes les données disponibles sauf les 3 derniers jours :
- les jours J−3 à J−1 servent au calibrage et au choix entre régression logistique et LightGBM ;
- le dernier jour (J−1 → J), vu ni par le nouveau modèle (« challenger ») ni par celui en place
  (« champion »), sert d'examen.

Le challenger n'est promu que s'il fait au moins aussi bien que le champion sur ce jour-là (à une
petite tolérance près) sans être moins bien calibré. Sinon le champion reste en place. Le service
de prédictions en direct lit ``champion.json`` et recharge les modèles dès qu'il change.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .features import DURATION_NUM, FIGHTER_CATS, FINISH_NUM, WINNER_NUM, round_features
from .models import IdentityCalibrator, make_logreg
from .pipelines import Config, _apply, _full_proba, _scores, _select_model, duration_frame, duration_lines
from .split import before, between, split_by_match_order
from .targets import FINISH_CLASSES, FINISH_INDEX, MK3, MKX

HOLDOUT = pd.Timedelta(days=1)
VALIDATION = pd.Timedelta(days=2)
TOLERANCE_LOG_LOSS = 0.002
TOLERANCE_ECE = 0.01
MIN_HOLDOUT = 100
CHAMPION_FILE = "champion.json"
PRODUCTION_DIR = "production"


def _history_masks(feat: pd.DataFrame, as_of: pd.Timestamp, lag: pd.Timedelta):
    ds = feat["date_start"]
    hold_start = as_of - HOLDOUT
    valid_start = hold_start - VALIDATION
    train = before(ds, valid_start, lag).to_numpy()
    valid = between(ds, valid_start, hold_start, lag)
    calib, select = split_by_match_order(feat.loc[valid, "game_id"], ds[valid], 0.5, pd.Timedelta(0))
    calib_m, select_m = valid.copy(), valid.copy()
    calib_m[valid], select_m[valid] = calib.to_numpy(), select.to_numpy()
    holdout = between(ds, hold_start, as_of, lag).to_numpy()
    return train, calib_m.to_numpy(), select_m.to_numpy(), holdout


def _history_candidate(key, feat, y_col, num, cats, n_classes, as_of, cfg) -> dict:
    feat = feat[feat[y_col].notna()].sort_values(["date_start", "game_id", "round_no"]).reset_index(drop=True)
    y = feat[y_col].astype(int).to_numpy()
    train, calib, select, holdout = _history_masks(feat, as_of, cfg.lag)
    name, model, cal, _ = _select_model(feat, y, num, cats, n_classes, train, calib, select, cfg)
    return {"key": key, "n_classes": n_classes, "n_train": int(train.sum()),
            "obj": {"nom": name, "modele": model, "calibrateur": cal, "variables": num + cats},
            "X_holdout": feat.loc[holdout, num + cats].reset_index(drop=True), "y_holdout": y[holdout]}


def train_production(data: dict, as_of: pd.Timestamp, cfg: Config) -> list[dict]:
    """Un challenger par cible : vainqueur (MKX, MK3), finish (MK3), durée (MKX)."""
    out = []
    for league in (MKX, MK3):
        d = data.get(league)
        if d is None:
            continue
        feat = round_features(d["matches"], d["rounds"], lag=cfg.lag)
        feat["y_winner"] = (feat["winner"] == 1).astype(float)
        feat["y_finish"] = feat["finish"].map(FINISH_INDEX)
        out.append(_history_candidate(f"vainqueur_{league}", feat, "y_winner", WINNER_NUM, FIGHTER_CATS, 2, as_of, cfg))
        if league == MK3:
            out.append(_history_candidate(f"finish_{league}", feat, "y_finish", FINISH_NUM, FIGHTER_CATS + ["prev_finish"],
                                          len(FINISH_CLASSES), as_of, cfg))
        if league == MKX and "odds" in d:
            lines, _ = duration_lines(d["odds"], d["round_ends"])
            df, _, _, _, y = duration_frame(feat, lines, as_of, cfg.lag)
            if len(df) == 0:
                continue
            ds = df["date_start"]
            train = before(ds, as_of - HOLDOUT, cfg.lag).to_numpy()
            holdout = between(ds, as_of - HOLDOUT, as_of, cfg.lag).to_numpy()
            if train.sum() < 50 or len(np.unique(y[train])) < 2:
                continue
            cats = ["prev_finish"]
            model = make_logreg(DURATION_NUM, cats).fit(df.loc[train, DURATION_NUM + cats], y[train])
            out.append({"key": f"duree_{league}", "n_classes": 2, "n_train": int(train.sum()),
                        "obj": {"nom": "régression logistique (inclut la cote du marché)", "modele": model,
                                "calibrateur": IdentityCalibrator(), "variables": DURATION_NUM + cats},
                        "X_holdout": df.loc[holdout, DURATION_NUM + cats].reset_index(drop=True), "y_holdout": y[holdout]})
    return out


def evaluate(obj: dict, X: pd.DataFrame, y: np.ndarray, n_classes: int) -> dict:
    binary = n_classes == 2
    P = _apply(obj["calibrateur"], _full_proba(obj["modele"], X[obj["variables"]], n_classes), binary)
    return _scores(y, P, binary)


def read_champions(artifacts_dir: Path) -> dict:
    try:
        return json.loads((Path(artifacts_dir) / CHAMPION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_champions(artifacts_dir: Path) -> tuple[str, dict]:
    """(signature, modèles) des champions en place ; la signature change à chaque promotion."""
    champions = read_champions(artifacts_dir)
    models = {}
    for key, info in champions.items():
        path = Path(artifacts_dir) / PRODUCTION_DIR / info["run"] / f"{key}.joblib"
        if path.exists():
            models[key] = {**joblib.load(path), "version": info["run"]}
    return json.dumps({k: v["run"] for k, v in sorted(champions.items())}), models


def promote(artifacts_dir: Path, run_id: str, candidates: list[dict], now: pd.Timestamp | None = None) -> list[dict]:
    """Enregistre chaque challenger, le compare au champion sur le jour d'examen, et le promeut
    seulement s'il fait au moins aussi bien. Retourne les décisions (pour le rapport et le bilan)."""
    artifacts_dir = Path(artifacts_dir)
    now = now or pd.Timestamp.now(tz="UTC")
    champions = read_champions(artifacts_dir)
    run_dir = artifacts_dir / PRODUCTION_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    decisions = []
    for c in candidates:
        key, n = c["key"], len(c["y_holdout"])
        joblib.dump(c["obj"], run_dir / f"{key}.joblib")
        chal = evaluate(c["obj"], c["X_holdout"], c["y_holdout"], c["n_classes"]) if n else None
        current = champions.get(key)
        champ = None
        if current is not None and n:
            path = artifacts_dir / PRODUCTION_DIR / current["run"] / f"{key}.joblib"
            if path.exists():
                champ = evaluate(joblib.load(path), c["X_holdout"], c["y_holdout"], c["n_classes"])
        if current is None or champ is None:
            promoted, reason = True, "premier modèle de production" if current is None else "champion illisible, remplacé"
        elif n < MIN_HOLDOUT:
            promoted, reason = False, f"jour d'examen trop court ({n} manches) : champion conservé"
        elif (chal["log_loss"] <= champ["log_loss"] + TOLERANCE_LOG_LOSS
              and chal["ece_quantile"] <= champ["ece_quantile"] + TOLERANCE_ECE):
            promoted, reason = True, "au moins aussi bon que le champion sur le jour d'examen"
        else:
            promoted, reason = False, "moins bon que le champion sur le jour d'examen : champion conservé"
        if promoted:
            champions[key] = {"run": run_id, "promu_le": now.isoformat(), "modele": c["obj"]["nom"],
                              "log_loss_examen": None if chal is None else chal["log_loss"], "n_examen": n}
        decisions.append({"cible": key, "promu": promoted, "raison": reason, "modele": c["obj"]["nom"],
                          "n_entrainement": c["n_train"], "n_examen": n,
                          "log_loss_challenger": None if chal is None else chal["log_loss"],
                          "log_loss_champion": None if champ is None else champ["log_loss"]})
    tmp = artifacts_dir / (CHAMPION_FILE + ".tmp")
    tmp.write_text(json.dumps(champions, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(artifacts_dir / CHAMPION_FILE)
    return decisions
