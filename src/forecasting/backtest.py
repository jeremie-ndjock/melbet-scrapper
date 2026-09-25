"""Backtest de value bets à mise fixe, aux cotes de clôture réellement proposées (plan, 8.4).

On ne parie que si la probabilité du modèle dépasse la probabilité du marché marge retirée d'au
moins ``tau``, au plus une fois par manche (l'issue d'espérance la plus haute). Aucune
progression selon les pertes précédentes.
"""
from __future__ import annotations

import numpy as np

from .metrics import n_required

STAKE = 1000.0
TAU_GRID = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08)


def value_bets(P_model, P_fair, odds, y, tau: float) -> dict:
    """``P_model``, ``P_fair``, ``odds`` : (n, k) ; ``y`` : indice de l'issue réalisée. Retourne
    les paris joués (indices de ligne, issue choisie, cote, gain)."""
    P_model, P_fair, odds = (np.asarray(a, float) for a in (P_model, P_fair, odds))
    y = np.asarray(y, int)
    edge = P_model - P_fair
    ev = P_model * odds - 1.0
    ev = np.where(edge > tau, ev, -np.inf)
    pick = ev.argmax(axis=1)
    rows = np.flatnonzero(np.isfinite(ev[np.arange(len(ev)), pick]) & (ev[np.arange(len(ev)), pick] > 0))
    chosen = pick[rows]
    o = odds[rows, chosen]
    won = y[rows] == chosen
    profit = np.where(won, STAKE * (o - 1.0), -STAKE)
    return {"rows": rows, "choice": chosen, "odds": o, "won": won, "profit": profit}


def by_outcome(bets: dict, labels: list[str]) -> dict:
    """Détail du backtest par issue pariée : sur un marché à 7 issues, quelques paris sur des
    issues rares à grosse cote peuvent à eux seuls faire ou défaire le résultat."""
    out = {}
    for k, label in enumerate(labels):
        mask = bets["choice"] == k
        if mask.any():
            out[label] = {"n_paris": int(mask.sum()), "resultat": float(bets["profit"][mask].sum()),
                          "rendement": float(bets["profit"][mask].mean() / STAKE),
                          "cote_moyenne": float(bets["odds"][mask].mean())}
    return out


def summarize(bets: dict, clusters, n_boot: int = 2000, seed: int = 0, target_edge: float = 0.03) -> dict:
    """Résultat du backtest avec intervalle de confiance du rendement (bootstrap par match) et
    le nombre de paris nécessaire pour trancher sur un avantage de ``target_edge``."""
    profit, odds = bets["profit"], bets["odds"]
    n = len(profit)
    if n == 0:
        return {"n_paris": 0, "mise_totale": 0.0, "resultat": 0.0, "rendement": None, "ic_bas": None,
                "ic_haut": None, "taux_reussite": None, "cote_moyenne": None, "creux_max": 0.0,
                "n_requis": None, "echantillon_insuffisant": True}
    ret = profit / STAKE
    cl = np.asarray(clusters)[bets["rows"]]
    uniq, inv = np.unique(cl, return_inverse=True)
    sums = np.bincount(inv, weights=ret, minlength=len(uniq))
    counts = np.bincount(inv, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    cum = np.cumsum(profit)
    sd = float(ret.std(ddof=1)) if n > 1 else float("nan")
    need = n_required(target_edge, sd) if n > 1 and sd > 0 else None
    return {
        "n_paris": n, "mise_totale": float(n * STAKE), "resultat": float(profit.sum()),
        "rendement": float(ret.mean()), "ic_bas": float(lo), "ic_haut": float(hi),
        "taux_reussite": float(bets["won"].mean()), "cote_moyenne": float(odds.mean()),
        "creux_max": float((np.maximum.accumulate(np.concatenate([[0.0], cum])) - np.concatenate([[0.0], cum])).max()),
        "n_requis": need, "echantillon_insuffisant": need is None or n < need,
    }


def choose_tau(P_model, P_fair, odds, y, min_bets: int = 50, default: float = 0.02) -> float:
    """Seuil d'avantage choisi sur la période de validation (jamais sur le test) : meilleur
    rendement parmi les seuils donnant au moins ``min_bets`` paris, sinon ``default``."""
    best, best_roi = default, -np.inf
    for tau in TAU_GRID:
        bets = value_bets(P_model, P_fair, odds, y, tau)
        if len(bets["profit"]) >= min_bets:
            roi = bets["profit"].mean() / STAKE
            if roi > best_roi:
                best, best_roi = tau, roi
    return best
