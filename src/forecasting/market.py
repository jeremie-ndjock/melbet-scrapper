"""Cotes du marché : décodage, cote de clôture réellement jouable, retrait de la marge, ligne
principale.

Une cote de clôture fiable ne peut pas se lire ligne par ligne : ``odds_snapshots`` n'enregistre
que les changements, et une sélection peut être retirée pendant que l'autre bouge encore. On
reconstitue donc l'état complet du marché à chaque instant et on ne garde que les instants où
toutes les sélections sont proposées ensemble et non suspendues.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from .targets import G_DURATION

_UNAVAILABLE = -1.0  # retirée ou suspendue ; un NaN serait écrasé par le report de valeur


def decode_param(g: pd.Series, param: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(manche, ligne) depuis ``param`` : manche × 100 + ligne / 100 pour la durée (g=1074),
    numéro de manche pour les autres marchés de manche. Fiable même sur les lignes « retirée »,
    dont ``round_no`` et ``line`` sont vides en base (vérifié le 2026-09-25)."""
    param = param.astype(float)
    is_dur = g == G_DURATION
    rnd = np.where(is_dur, np.floor(param / 100 + 1e-9), np.rint(param))
    line = np.where(is_dur, np.round((param - 100 * rnd) * 100, 2), -1.0)
    return pd.Series(rnd.astype(int), index=param.index), pd.Series(line, index=param.index)


def closing_quotes(odds: pd.DataFrame, required_t: dict[int, tuple[int, ...]]) -> pd.DataFrame:
    """Une ligne par marché (game_id, g, round_no, line) avec la cote d'ouverture et de clôture de
    chaque sélection requise, prises au même instant, plus le nombre de changements d'état.

    ``odds`` : colonnes game_id, g, t, param, odds (NaN = retirée), blocked, ts_server.
    Un marché dont les sélections requises n'ont jamais coexisté est absent du résultat."""
    cols = ["game_id", "g", "round_no", "line"]
    if odds.empty:
        return pd.DataFrame(columns=cols)
    df = odds.copy()
    df["round_no"], df["line"] = decode_param(df["g"], df["param"])
    df["val"] = df["odds"].where(df["odds"].notna() & ~df["blocked"].astype(bool), _UNAVAILABLE)
    out = []
    for g, part in df.groupby("g", sort=True):
        needed = list(required_t[g])
        part = part[part["t"].isin(needed)]
        wide = part.pivot_table(index=cols + ["ts_server"], columns="t", values="val", aggfunc="last")
        wide = wide.reindex(columns=needed)
        wide = wide.groupby(level=cols, sort=False).ffill()
        ok = (wide > 0).all(axis=1)
        valid = wide[ok].reset_index()
        if valid.empty:
            continue
        grouped = valid.groupby(cols, sort=True)
        first, last = grouped.head(1).set_index(cols), grouped.tail(1).set_index(cols)
        res = pd.DataFrame(index=last.index)
        res["ts_open"], res["ts_close"] = first["ts_server"], last["ts_server"]
        for t in needed:
            res[f"open_{t}"], res[f"close_{t}"] = first[t], last[t]
        res["n_changes"] = grouped.size()
        out.append(res.reset_index())
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)


def devig(odds_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Retrait multiplicatif de la marge : p_i = (1/o_i) / Σ 1/o_j. Retourne (probabilités,
    marge). Jamais 1/cote brut, qui contient la marge du bookmaker."""
    inv = 1.0 / np.asarray(odds_matrix, dtype=float)
    total = inv.sum(axis=1, keepdims=True)
    return inv / total, total[:, 0] - 1.0


def devig_power(odds_matrix: np.ndarray) -> np.ndarray:
    """Méthode « power » (p_i = (1/o_i)^k avec Σ = 1) : contrôle de sensibilité, la méthode
    multiplicative flattant les issues rares (Hara-Kiri, Animality)."""
    inv = 1.0 / np.asarray(odds_matrix, dtype=float)
    out = np.empty_like(inv)
    for i, row in enumerate(inv):
        k = brentq(lambda x: np.sum(row ** x) - 1.0, 0.5, 5.0)
        out[i] = row ** k
    return out


def main_line(duration_quotes: pd.DataFrame, t_over: int, t_under: int) -> pd.DataFrame:
    """Ligne principale de chaque manche : celle où les cotes « plus » et « moins » sont les plus
    proches à la clôture (``is_center`` n'est jamais renseigné par le site pour ce marché). En
    cas d'égalité, la ligne la plus basse, pour un choix déterministe."""
    if duration_quotes.empty:
        return duration_quotes.copy()
    df = duration_quotes.copy()
    df["_gap"] = (df[f"close_{t_over}"] - df[f"close_{t_under}"]).abs()
    df = df.sort_values(["game_id", "round_no", "_gap", "line"])
    return df.groupby(["game_id", "round_no"], sort=True).head(1).drop(columns="_gap").reset_index(drop=True)
