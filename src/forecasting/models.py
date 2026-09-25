"""Modèles, calibrateurs et combinaison modèle + marché.

LightGBM tourne sur un seul fil et en mode déterministe : le collecteur doit garder la priorité
sur les 2 vCPU du VPS, et deux exécutions identiques doivent donner le même rapport.

Aucune pondération des classes rares (MK3) : elle fausserait les probabilités, alors que le
critère de choix est leur calibration (plan, section 7.3).
"""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.special import expit, logit, log_softmax, softmax
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

EPS = 1e-6


def make_logreg(num_cols: list[str], cat_cols: list[str], C: float = 1.0) -> Pipeline:
    pre = ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median", add_indicator=True)),
                          ("sc", StandardScaler())]), num_cols),
        ("cat", OneHotEncoder(handle_unknown="ignore"), cat_cols),
    ], sparse_threshold=0.0)
    return Pipeline([("pre", pre), ("lr", LogisticRegression(C=C, max_iter=3000))])


def lgbm_params(n_classes: int, seed: int = 0) -> dict:
    params = dict(n_estimators=2000, learning_rate=0.03, num_leaves=31, min_child_samples=200,
                  reg_lambda=5.0, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                  n_jobs=1, deterministic=True, force_row_wise=True, random_state=seed, verbose=-1)
    params["objective"] = "binary" if n_classes == 2 else "multiclass"
    return params


class LGBMModel:
    """LightGBM avec arrêt précoce sur la fin (chronologique) de l'ensemble d'entraînement et
    identités des combattants en variables catégorielles."""

    def __init__(self, num_cols: list[str], cat_cols: list[str], n_classes: int, seed: int = 0):
        self.num_cols, self.cat_cols, self.n_classes, self.seed = num_cols, cat_cols, n_classes, seed
        self.categories: dict[str, list] = {}
        self.model: lgb.LGBMClassifier | None = None

    def _frame(self, X: pd.DataFrame) -> pd.DataFrame:
        out = X[self.num_cols].astype(float).copy()
        for c in self.cat_cols:
            out[c] = pd.Categorical(X[c].astype(str), categories=self.categories[c])
        return out

    def fit(self, X: pd.DataFrame, y: np.ndarray, eval_fraction: float = 0.1) -> LGBMModel:
        self.categories = {c: sorted(X[c].astype(str).unique()) for c in self.cat_cols}
        n_eval = max(int(len(X) * eval_fraction), 1)
        Xf = self._frame(X)
        self.model = lgb.LGBMClassifier(**lgbm_params(self.n_classes, self.seed))
        self.model.fit(Xf.iloc[:-n_eval], y[:-n_eval], eval_X=(Xf.iloc[-n_eval:],), eval_y=(y[-n_eval:],),
                       categorical_feature=self.cat_cols or "auto",
                       callbacks=[lgb.early_stopping(100, verbose=False)])
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._frame(X))

    @property
    def classes_(self) -> np.ndarray:
        return self.model.classes_

    @property
    def n_iterations(self) -> int:
        return int(self.model.best_iteration_ or self.model.n_estimators)


def positive_proba(model, X: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(X)[:, 1]


class PlattCalibrator:
    """Recalibration binaire : sigmoid(a·logit(p) + b)."""

    def fit(self, p, y) -> PlattCalibrator:
        z = logit(np.clip(np.asarray(p, float), EPS, 1 - EPS)).reshape(-1, 1)
        self.lr = LogisticRegression(C=1e6, max_iter=1000).fit(z, np.asarray(y, int))
        return self

    def transform(self, p) -> np.ndarray:
        z = logit(np.clip(np.asarray(p, float), EPS, 1 - EPS)).reshape(-1, 1)
        return self.lr.predict_proba(z)[:, 1]


class TemperatureCalibrator:
    """Recalibration multiclasse : softmax(log p / T), un seul paramètre."""

    def fit(self, P, y) -> TemperatureCalibrator:
        logp = np.log(np.clip(np.asarray(P, float), EPS, 1.0))
        y = np.asarray(y, int)

        def nll(t):
            return -log_softmax(logp / t, axis=1)[np.arange(len(y)), y].mean()
        self.t = float(minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded").x)
        return self

    def transform(self, P) -> np.ndarray:
        return softmax(np.log(np.clip(np.asarray(P, float), EPS, 1.0)) / self.t, axis=1)


class LogLinearPool:
    """Combinaison modèle + marché : log p ∝ b·log p_marché + c·log p_modèle (plus une constante
    en binaire). ``use_model=False`` donne la référence « marché seul recalibré » : si le modèle
    n'apporte rien, c ≈ 0 et les deux se valent (plan, section 7.3)."""

    def __init__(self, binary: bool, use_model: bool = True):
        self.binary, self.use_model = binary, use_model
        self.coef: np.ndarray | None = None

    def _design(self, p_market, p_model):
        if self.binary:
            cols = [np.ones(len(p_market)), logit(np.clip(p_market, EPS, 1 - EPS))]
            if self.use_model:
                cols.append(logit(np.clip(p_model, EPS, 1 - EPS)))
            return np.column_stack(cols)
        parts = [np.log(np.clip(p_market, EPS, 1.0))]
        if self.use_model:
            parts.append(np.log(np.clip(p_model, EPS, 1.0)))
        return np.stack(parts, axis=-1)  # (n, k, n_params)

    def _proba(self, coef, D):
        if self.binary:
            return expit(D @ coef)
        return softmax(D @ coef, axis=1)

    def fit(self, p_market, p_model, y) -> LogLinearPool:
        D = self._design(np.asarray(p_market, float), None if p_model is None else np.asarray(p_model, float))
        y = np.asarray(y, int)

        def nll(coef):
            p = self._proba(coef, D)
            if self.binary:
                p = np.clip(p, 1e-12, 1 - 1e-12)
                return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)) + 1e-6 * coef @ coef
            return -np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1.0))) + 1e-6 * coef @ coef
        x0 = np.zeros(D.shape[-1])
        x0[1 if self.binary else 0] = 1.0
        self.coef = minimize(nll, x0, method="BFGS").x
        return self

    def predict(self, p_market, p_model=None) -> np.ndarray:
        D = self._design(np.asarray(p_market, float), None if p_model is None else np.asarray(p_model, float))
        return self._proba(self.coef, D)

    @property
    def model_weight(self) -> float:
        return float(self.coef[-1]) if self.use_model else 0.0


def pool_weight_ci(p_market, p_model, y, clusters, binary: bool, n_boot: int = 300, seed: int = 0) -> tuple[float, float]:
    """Intervalle de confiance à 95 % du poids c du modèle, par bootstrap de matchs entiers."""
    clusters = np.asarray(clusters)
    uniq, inv = np.unique(clusters, return_inverse=True)
    members = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    rng = np.random.default_rng(seed)
    weights = []
    for _ in range(n_boot):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])
        pool = LogLinearPool(binary).fit(np.asarray(p_market)[idx], np.asarray(p_model)[idx], np.asarray(y)[idx])
        weights.append(pool.model_weight)
    lo, hi = np.quantile(weights, [0.025, 0.975])
    return float(lo), float(hi)
