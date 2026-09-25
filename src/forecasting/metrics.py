"""Métriques de qualité des probabilités (le critère de choix des modèles est la calibration et
la log-loss, jamais la précision : Walsh & Joshi 2024, plan d'entraînement section 7.3)."""
from __future__ import annotations

import math

import numpy as np

EPS = 1e-15


def as_matrix(p) -> np.ndarray:
    """Probabilités sous forme (n, k) : un vecteur binaire devient [1 − p, p]."""
    p = np.asarray(p, dtype=float)
    return np.column_stack([1 - p, p]) if p.ndim == 1 else p


def row_log_loss(y, p) -> np.ndarray:
    """Log-loss par ligne (utile pour les intervalles de confiance appariés)."""
    P = np.clip(as_matrix(p), EPS, 1.0)
    y = np.asarray(y, dtype=int)
    return -np.log(P[np.arange(len(y)), y])


def log_loss(y, p) -> float:
    return float(row_log_loss(y, p).mean())


def brier(y, p) -> float:
    """Brier multiclasse (somme sur les classes) ; en binaire, 2 × le Brier usuel."""
    P = as_matrix(p)
    Y = np.zeros_like(P)
    Y[np.arange(len(P)), np.asarray(y, dtype=int)] = 1.0
    return float(((P - Y) ** 2).sum(axis=1).mean())


def classwise_ece(p, y, n_bins: int = 20, strategy: str = "quantile") -> dict:
    """Erreur de calibration par classe : pour chaque classe, écart moyen pondéré entre
    probabilité prédite et fréquence observée, par intervalle.

    ``strategy="uniform"`` : 20 intervalles de même largeur, avec la part d'intervalles non vides
    (la règle « ≥ 80 % non vides » de l'étude de Bath est intenable sur nos marchés, même pour le
    bookmaker : probabilités de vainqueur entre ≈ 0,25 et 0,75). ``"quantile"`` : intervalles de
    même effectif, retenu comme garde-fou (plan, section 7.3). En binaire, seule la classe 1 est
    évaluée (la classe 0 donnerait exactement la même valeur)."""
    P = as_matrix(p)
    y = np.asarray(y, dtype=int)
    classes = [1] if np.asarray(p).ndim == 1 else range(P.shape[1])
    per_class, nonempty = [], []
    for k in classes:
        pk, yk = P[:, k], (y == k).astype(float)
        if strategy == "uniform":
            edges = np.linspace(0.0, 1.0, n_bins + 1)
        else:
            edges = np.unique(np.quantile(pk, np.linspace(0.0, 1.0, n_bins + 1)))
            if len(edges) < 2:  # probabilités toutes identiques (ex. cote fixe) : un seul intervalle
                edges = np.array([pk.min(), pk.max() + 1e-12])
        idx = np.clip(np.searchsorted(edges, pk, side="right") - 1, 0, len(edges) - 2)
        err, filled = 0.0, 0
        for b in range(len(edges) - 1):
            mask = idx == b
            if mask.any():
                filled += 1
                err += mask.sum() / len(pk) * abs(pk[mask].mean() - yk[mask].mean())
        per_class.append(err)
        nonempty.append(filled / (len(edges) - 1))
    return {"ece": float(np.mean(per_class)), "per_class": [float(e) for e in per_class],
            "nonempty_frac": float(np.mean(nonempty))}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    ph = k / n
    denom = 1 + z * z / n
    center = (ph + z * z / (2 * n)) / denom
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def calibration_table(p, y, n_bins: int = 10) -> list[dict]:
    """Table de calibration binaire à intervalles de même effectif, avec intervalle de Wilson."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=int)
    order = np.argsort(p, kind="stable")
    rows = []
    for chunk in np.array_split(order, min(n_bins, len(p))):
        if len(chunk) == 0:
            continue
        k, n = int(y[chunk].sum()), len(chunk)
        lo, hi = wilson(k, n)
        rows.append({"p_moyen": float(p[chunk].mean()), "freq_observee": k / n, "n": n, "ic_bas": lo, "ic_haut": hi})
    return rows


def paired_bootstrap(loss_a, loss_b, clusters, alpha: float = 0.05, n_boot: int = 2000, seed: int = 0) -> dict:
    """Intervalle de confiance de la différence moyenne ``loss_a − loss_b`` par bootstrap en
    rééchantillonnant des matchs entiers (les manches d'un même match sont corrélées)."""
    diff = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    clusters = np.asarray(clusters)
    uniq, inv = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inv, weights=diff, minlength=len(uniq))
    counts = np.bincount(inv, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return {"moyenne": float(diff.mean()), "ic_bas": float(lo), "ic_haut": float(hi), "alpha": alpha}


def n_required(edge: float, sd: float, z: float = 1.96) -> int:
    """Nombre de paris pour qu'un avantage ``edge`` dépasse ``z`` écarts-types du hasard."""
    return int(math.ceil((z * sd / edge) ** 2))
