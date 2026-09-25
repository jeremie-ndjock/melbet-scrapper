"""Garde-fous sur les données avant tout entraînement : les matchs douteux sont exclus et
comptés, jamais corrigés ni devinés.

Le plus important : l'orientation joueur 1 / joueur 2. Le vainqueur d'une manche peut venir du
direct (``live_feed.py``) ou du rattrapage (``results.py``), fusionnés par un COALESCE où la
première valeur écrite gagne. Si les deux sources n'orientaient pas le match de la même façon,
les étiquettes seraient inversées sans bruit : on vérifie donc que les manches gagnées par
chaque côté redonnent exactement le score final officiel.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .targets import finish_label


def fighter_key(ids: pd.Series, names: pd.Series) -> pd.Series:
    """Identifiant du combattant fourni par le site, sinon son nom (jamais un nom d'affichage
    d'une autre table)."""
    by_id = ids.astype("Int64").astype(str)
    return by_id.where(ids.notna(), "nom:" + names.astype(str))


def clean(matches: pd.DataFrame, rounds: pd.DataFrame, events: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Retourne (matchs retenus, manches retenues, rapport de qualité)."""
    m = matches.copy()
    r = rounds[rounds["game_id"].isin(m["game_id"])].copy()
    report: dict = {"matchs_bruts": int(len(m)), "manches_brutes": int(len(r))}

    ties = m["winner"].isna()
    report["exclus_egalite"] = int(ties.sum())
    m = m[~ties]

    bad_rounds = r.groupby("game_id")["winner"].apply(lambda w: w.isna().any() | ~w.isin([1, 2]).all())
    per_game = r.groupby("game_id").agg(n=("round_no", "size"), first=("round_no", "min"), last=("round_no", "max"),
                                        w1=("winner", lambda w: int((w == 1).sum())),
                                        w2=("winner", lambda w: int((w == 2).sum())))
    gaps = per_game.index[(per_game["first"] != 1) | (per_game["last"] != per_game["n"])]
    report["exclus_trous_manches"] = int(m["game_id"].isin(gaps).sum())
    report["exclus_vainqueur_manche_inconnu"] = int(m["game_id"].isin(bad_rounds.index[bad_rounds]).sum())
    joined = m.merge(per_game[["w1", "w2"]], left_on="game_id", right_index=True, how="left")
    mismatch = joined["game_id"][(joined["w1"] != joined["final_score1"]) | (joined["w2"] != joined["final_score2"])]
    report["exclus_score_incoherent"] = int(len(mismatch))
    report["exclus_sans_manches"] = int((~m["game_id"].isin(per_game.index)).sum())

    orient_bad: set = set()
    if events is not None and not events.empty:
        e = events.merge(m[["game_id", "p1_id", "p2_id"]], on="game_id", suffixes=("_ev", "_res"))
        known = e["p1_id_ev"].notna() & e["p1_id_res"].notna()
        orient_bad = set(e.loc[known & (e["p1_id_ev"] != e["p1_id_res"]), "game_id"])
        report["matchs_compares_direct"] = int(known.sum())
    report["exclus_orientation_inversee"] = len(orient_bad)

    drop = set(gaps) | set(bad_rounds.index[bad_rounds]) | set(mismatch) | orient_bad
    m = m[~m["game_id"].isin(drop) & m["game_id"].isin(per_game.index)].copy()
    r = r[r["game_id"].isin(m["game_id"])].copy()

    m["p1_key"] = fighter_key(m["p1_id"], m["p1_name"])
    m["p2_key"] = fighter_key(m["p2_id"], m["p2_name"])
    names_per_id = m.groupby("p1_key")["p1_name"].nunique()
    report["identifiants_a_plusieurs_noms"] = int((names_per_id > 1).sum())

    r["finish"] = [finish_label(c, d) for c, d in zip(r["finish_code"], r["finish_di"])]
    both = r["finish_code"].notna() & r["finish_di"].notna()
    disagree = both & (r["finish_code"] != r["finish_di"].map(lambda d: finish_label(None, d)))
    report["finish_desaccord_direct_officiel"] = int(disagree.sum())
    report["manches_sans_finish"] = int(r["finish"].isna().sum())
    report["matchs_retenus"] = int(len(m))
    report["manches_retenues"] = int(len(r))
    return m.sort_values(["date_start", "game_id"]).reset_index(drop=True), r.reset_index(drop=True), report


def duration_coverage(rounds: pd.DataFrame) -> dict:
    """Part des manches ayant une durée connue, par numéro de manche (la dernière manche d'un
    match manque plus souvent : le rapport doit le montrer plutôt que l'imputer)."""
    live = rounds[rounds["game_id"].isin(rounds.loc[rounds["seconds"].notna(), "game_id"])]
    if live.empty:
        return {}
    cov = live.groupby("round_no")["seconds"].apply(lambda s: float(s.notna().mean()))
    return {int(k): round(v, 4) for k, v in cov.items()}


def match_lag(events: pd.DataFrame, default: pd.Timedelta = pd.Timedelta(minutes=18)) -> tuple[pd.Timedelta, dict]:
    """Délai de disponibilité d'un match = 99e centile mesuré de sa durée (fin − début), au
    moins la valeur par défaut."""
    if events is None or events.empty or events["finished_at"].isna().all():
        return default, {"p99_duree_match_s": None}
    dur = (events["finished_at"] - events["start_ts"]).dt.total_seconds().dropna()
    p99 = float(np.quantile(dur, 0.99)) if len(dur) else None
    lag = max(default, pd.Timedelta(seconds=p99)) if p99 else default
    return lag, {"p99_duree_match_s": p99, "mediane_duree_match_s": float(np.median(dur)) if len(dur) else None}
