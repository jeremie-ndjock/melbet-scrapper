"""Découpages temporels par match, avec purge et embargo (plan, section 9).

Jamais de découpage aléatoire, jamais une manche d'un match d'un côté et une autre de l'autre
côté. Purge : un match d'entraînement ne doit pas encore être en cours au début de la période
évaluée (``date_start + lag`` ≤ coupure). Embargo : on laisse un court intervalle vide après la
coupure avant de commencer à évaluer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def before(date_start: pd.Series, cut: pd.Timestamp, lag: pd.Timedelta) -> pd.Series:
    """Matchs entièrement terminés avant ``cut`` (purgés)."""
    return (date_start + lag) <= cut


def between(date_start: pd.Series, start: pd.Timestamp, end: pd.Timestamp, lag: pd.Timedelta | None = None) -> pd.Series:
    """Matchs commençant dans [start, end[ ; avec ``lag``, seulement ceux terminés avant ``end``."""
    mask = (date_start >= start) & (date_start < end)
    if lag is not None:
        mask &= (date_start + lag) <= end
    return mask


def split_by_match_order(game_ids: pd.Series, date_start: pd.Series, fraction: float,
                         embargo: pd.Timedelta) -> tuple[pd.Series, pd.Series]:
    """Coupe une période en deux selon l'ordre chronologique des matchs : les premiers
    ``fraction`` des matchs d'un côté, le reste de l'autre après ``embargo``."""
    firsts = pd.DataFrame({"game_id": game_ids, "date_start": date_start}).drop_duplicates("game_id")
    firsts = firsts.sort_values(["date_start", "game_id"])
    n_first = int(round(len(firsts) * fraction))
    if n_first == 0 or n_first == len(firsts):
        empty = pd.Series(False, index=game_ids.index)
        return (empty | True, empty) if n_first else (empty, empty | True)
    cut = firsts["date_start"].iloc[n_first]
    first_part = date_start < cut
    second_part = date_start >= cut + embargo
    return first_part, second_part


def match_blocks(game_ids: pd.Series, date_start: pd.Series, n_blocks: int) -> tuple[pd.Series, list[pd.Timestamp]]:
    """Numéro de bloc (0..n−1) de chaque ligne, blocs de même nombre de matchs consécutifs, et
    l'heure de début de chaque bloc."""
    firsts = pd.DataFrame({"game_id": game_ids, "date_start": date_start}).drop_duplicates("game_id")
    firsts = firsts.sort_values(["date_start", "game_id"]).reset_index(drop=True)
    firsts["block"] = np.minimum((np.arange(len(firsts)) * n_blocks) // max(len(firsts), 1), n_blocks - 1)
    starts = firsts.groupby("block")["date_start"].min().tolist()
    block_of = dict(zip(firsts["game_id"], firsts["block"]))
    return game_ids.map(block_of), starts


def weekly_folds(date_start: pd.Series, first_test: pd.Timestamp, end: pd.Timestamp, lag: pd.Timedelta,
                 embargo: pd.Timedelta, step: pd.Timedelta = pd.Timedelta(days=7)) -> list[tuple[str, pd.Series, pd.Series]]:
    """Validation glissante : pour chaque semaine, entraînement sur tout ce qui précède
    (purgé), évaluation sur la semaine (après embargo)."""
    folds, start = [], first_test
    while start + step <= end:
        train = before(date_start, start, lag)
        test = between(date_start, start + embargo, start + step)
        folds.append((start.isoformat(), train, test))
        start += step
    return folds
