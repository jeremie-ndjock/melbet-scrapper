"""Règle de pari prudente pour le type de finish MK3, FIGÉE À L'AVANCE (Memoire.md, section 40).

Enregistrée le 2026-09-27, avant d'être jugée : elle ne sera évaluée que sur les manches jouées à
partir de ``JUDGED_FROM``, jamais sur les données qui ont servi à l'imaginer. Ne pas modifier
ces constantes : toute nouvelle variante est un NOUVEL essai, avec sa propre date d'enregistrement
(sinon le jugement n'a plus de valeur, voir le registre des essais, plan section 9).

Pourquoi ces choix (section 38) : la règle d'espérance maximale pariait surtout sur des issues
rares à grosse cote (Babality à 38 de cote moyenne), où le résultat relève de la loterie. On se
limite donc aux trois issues fréquentes et on exige un avantage net.

Jugement fictif, à mise fixe, aux cotes affichées au moment de la prédiction publiée (celles
qu'un lecteur du groupe aurait pu obtenir), jamais à une cote postérieure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .targets import FINISH_CLASSES

JUDGED_FROM = pd.Timestamp("2026-09-29 00:00", tz="UTC")
ALLOWED = ("R", "F", "B")
MIN_EDGE = 0.03
STAKE = 1000.0
MIN_BETS_FOR_VERDICT = 500
CONFIDENCE = 0.99  # consultée chaque jour : un niveau plus strict compense ces regards répétés

_ALLOWED_IDX = [FINISH_CLASSES.index(c) for c in ALLOWED]


def pick(P_model, P_fair, odds) -> tuple[int, float] | None:
    """Issue pariée (indice de classe, cote) selon la règle, ou ``None`` : parmi Regular,
    Fatality et Brutality, celle d'espérance la plus haute dont la probabilité du modèle dépasse
    celle du marché (marge retirée) d'au moins ``MIN_EDGE``."""
    if P_model is None or P_fair is None or odds is None:
        return None
    P_model, P_fair, odds = (np.asarray(a, float) for a in (P_model, P_fair, odds))
    best, best_ev = None, 0.0
    for k in _ALLOWED_IDX:
        if P_model[k] - P_fair[k] >= MIN_EDGE:
            ev = P_model[k] * odds[k] - 1.0
            if ev > best_ev:
                best, best_ev = k, ev
    return None if best is None else (best, float(odds[best]))


def judge(bets: list[dict], n_boot: int = 2000, seed: int = 0) -> dict:
    """Bilan cumulé des paris fictifs (``game_id``, ``profit``) : rendement, intervalle de
    confiance par bootstrap de matchs entiers, verdict."""
    n = len(bets)
    if n == 0:
        return {"n_paris": 0, "resultat": 0.0, "rendement": None, "ic_bas": None, "ic_haut": None,
                "verdict": "en attente de paris"}
    profit = np.array([b["profit"] for b in bets], float)
    games = np.array([b["game_id"] for b in bets])
    uniq, inv = np.unique(games, return_inverse=True)
    sums = np.bincount(inv, weights=profit / STAKE, minlength=len(uniq))
    counts = np.bincount(inv, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    alpha = 1 - CONFIDENCE
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    if n >= MIN_BETS_FOR_VERDICT and lo > 0:
        verdict = "avantage démontré"
    elif n >= MIN_BETS_FOR_VERDICT and hi < 0:
        verdict = "règle perdante : à abandonner"
    else:
        verdict = f"en cours ({n}/{MIN_BETS_FOR_VERDICT} paris minimum avant verdict)"
    return {"n_paris": n, "resultat": float(profit.sum()), "rendement": float(profit.mean() / STAKE),
            "ic_bas": float(lo), "ic_haut": float(hi), "verdict": verdict}
