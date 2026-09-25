"""Variables des modèles, toutes calculées uniquement à partir du passé de la manche prédite.

Règle anti-fuite (plan, section 4.6) : un match n'entre dans l'historique d'un combattant qu'à
son heure de disponibilité, ``date_start + lag`` (lag = durée maximale mesurée d'un match,
≈ 18 min au 99e centile). « Strictement avant ``date_start`` » ne suffirait pas : un match
commencé juste avant n'est pas forcément terminé au moment où l'on prédit.

Les variables intra-match (score avant la manche, manche précédente) sont légitimes : le marché
de la manche N est proposé après la fin de la manche N−1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .targets import FINISH_CLASSES

DEFAULT_LAG = pd.Timedelta(minutes=18)


def intra_match(rounds: pd.DataFrame) -> pd.DataFrame:
    """Score avant la manche, balles de match, vainqueur/finish/durée de la manche précédente,
    finishes déjà vus dans le match. ``rounds`` : game_id, round_no, winner, seconds, finish."""
    df = rounds.sort_values(["game_id", "round_no"]).reset_index(drop=True)
    g = df.groupby("game_id", sort=False)
    w1 = (df["winner"] == 1).astype(int)
    w2 = (df["winner"] == 2).astype(int)
    df["s1_before"] = w1.groupby(df["game_id"]).cumsum() - w1
    df["s2_before"] = w2.groupby(df["game_id"]).cumsum() - w2
    df["score_diff"] = df["s1_before"] - df["s2_before"]
    df["p1_match_point"] = (df["s1_before"] == 4).astype(int)  # victoire au premier à 5 manches
    df["p2_match_point"] = (df["s2_before"] == 4).astype(int)
    prev_winner = g["winner"].shift(1)
    df["prev_winner_p1"] = np.where(prev_winner.isna(), np.nan, (prev_winner == 1).astype(float))
    df["prev_finish"] = g["finish"].shift(1).fillna("aucun").astype(str)
    df["prev_seconds"] = g["seconds"].shift(1)
    for c in FINISH_CLASSES:
        hit = (df["finish"] == c).astype(int)
        df[f"match_{c}_so_far"] = hit.groupby(df["game_id"]).cumsum() - hit
    return df


def _contributions(matches: pd.DataFrame, rounds: pd.DataFrame, lag: pd.Timedelta) -> pd.DataFrame:
    """Une ligne par (match, côté) : ce que ce match apporte à l'historique du combattant."""
    r = rounds.copy()
    r["w1"] = (r["winner"] == 1).astype(int)
    r["w2"] = (r["winner"] == 2).astype(int)
    r["sec"] = r["seconds"].fillna(0.0)
    r["sec_n"] = r["seconds"].notna().astype(int)
    agg = {"n_rounds": ("round_no", "size"), "w1": ("w1", "sum"), "w2": ("w2", "sum"),
           "sec_sum": ("sec", "sum"), "sec_n": ("sec_n", "sum")}
    for c in FINISH_CLASSES:
        r[f"f1_{c}"] = ((r["winner"] == 1) & (r["finish"] == c)).astype(int)
        r[f"f2_{c}"] = ((r["winner"] == 2) & (r["finish"] == c)).astype(int)
        agg[f"f1_{c}"], agg[f"f2_{c}"] = (f"f1_{c}", "sum"), (f"f2_{c}", "sum")
    per_game = r.groupby("game_id").agg(**agg).reset_index()
    m = matches[["game_id", "league_id", "date_start", "p1_key", "p2_key", "winner"]].merge(per_game, on="game_id")
    sides = []
    for side, other in ((1, 2), (2, 1)):
        s = pd.DataFrame({
            "game_id": m["game_id"], "league_id": m["league_id"], "fighter": m[f"p{side}_key"],
            "avail_ts": m["date_start"] + lag, "played": m["n_rounds"], "won": m[f"w{side}"],
            "sec_sum": m["sec_sum"], "sec_n": m["sec_n"], "m_played": 1,
            "m_won": (m["winner"] == side).astype(int),
        })
        for c in FINISH_CLASSES:
            s[f"perf_{c}"], s[f"suff_{c}"] = m[f"f{side}_{c}"], m[f"f{other}_{c}"]
        sides.append(s)
    return pd.concat(sides, ignore_index=True)


def asof_cumulative(contrib: pd.DataFrame, by: str, query: pd.DataFrame, query_by: str,
                    stats: list[str], at: pd.Series | None = None) -> pd.DataFrame:
    """Somme des ``stats`` de ``contrib`` disponibles STRICTEMENT avant l'instant de chaque ligne
    de ``query`` (``date_start`` par défaut, ou ``at``), pour la même clé. Résultat aligné sur
    l'index de ``query`` ; 0 quand rien n'est encore disponible. Les contributions de même heure
    sont agrégées avant le cumul, et l'égalité exacte est exclue."""
    agg = contrib.groupby([by, "avail_ts"], sort=True)[stats].sum()
    cum = agg.groupby(level=0, sort=False).cumsum().reset_index()
    cum[by] = cum[by].astype(str)
    q = pd.DataFrame({"_key": query[query_by].astype(str).to_numpy(),
                      "_t": (at if at is not None else query["date_start"]).to_numpy(),
                      "_idx": np.arange(len(query))})
    q = q.sort_values("_t", kind="stable")
    cum = cum.sort_values("avail_ts", kind="stable")
    merged = pd.merge_asof(q, cum, left_on="_t", right_on="avail_ts", left_by="_key", right_by=by,
                           allow_exact_matches=False, direction="backward")
    merged = merged.sort_values("_idx")
    out = merged[stats].fillna(0.0).to_numpy()
    return pd.DataFrame(out, columns=stats, index=query.index)


def _shrink(num, den, prior, k):
    return (num + k * prior) / (den + k)


def match_features(matches: pd.DataFrame, rounds: pd.DataFrame, lag: pd.Timedelta = DEFAULT_LAG,
                   k: float = 20.0, series_hours: tuple[int, ...] = (3, 6, 12),
                   query: pd.DataFrame | None = None) -> pd.DataFrame:
    """Variables par match (historique causal de chaque combattant, face-à-face, taux de la
    ligue), une ligne par ``game_id`` de ``query`` (par défaut, tous les ``matches``). En direct,
    ``query`` ne contient que le match à prédire : l'historique vient de ``matches``."""
    contrib = _contributions(matches, rounds, lag)
    base_stats = ["played", "won", "sec_sum", "sec_n", "m_played", "m_won"]
    fin_stats = [f"perf_{c}" for c in FINISH_CLASSES] + [f"suff_{c}" for c in FINISH_CLASSES]
    stats = base_stats + fin_stats
    m = (matches if query is None else query).reset_index(drop=True)
    out = pd.DataFrame({"game_id": m["game_id"]})

    # Taux de la ligue (causaux), servant d'a priori au lissage et de variables de dérive.
    league = asof_cumulative(contrib, "league_id", m, "league_id", stats)
    league_rounds = league["won"]  # chaque manche jouée est gagnée par exactement un côté
    prior = {c: np.where(league_rounds > 0, league[f"perf_{c}"] / league_rounds.where(league_rounds > 0, 1),
                         1.0 / len(FINISH_CLASSES)) for c in FINISH_CLASSES}
    league_sec = np.where(league["sec_n"] > 0, league["sec_sum"] / league["sec_n"].where(league["sec_n"] > 0, 1), np.nan)
    for c in FINISH_CLASSES:
        out[f"league_prior_{c}"] = prior[c]
    out["league_mean_sec"] = league_sec

    for side in (1, 2):
        h = asof_cumulative(contrib, "fighter", m, f"p{side}_key", stats)
        week = asof_cumulative(contrib, "fighter", m, f"p{side}_key", ["played", "won"],
                               at=m["date_start"] - pd.Timedelta(days=7))
        p = f"p{side}_"
        out[p + "rounds_n"] = h["played"]
        out[p + "win_rate"] = _shrink(h["won"], h["played"], 0.5, k)
        out[p + "win_rate_7d"] = _shrink(h["won"] - week["won"], h["played"] - week["played"], 0.5, k)
        out[p + "match_win_rate"] = _shrink(h["m_won"], h["m_played"], 0.5, k / 4)
        lost = h["played"] - h["won"]
        for c in FINISH_CLASSES:
            out[p + f"perf_{c}"] = _shrink(h[f"perf_{c}"], h["won"], prior[c], k)
            out[p + f"suff_{c}"] = _shrink(h[f"suff_{c}"], lost, prior[c], k)
        fallback = np.where(np.isnan(league_sec), 0.0, league_sec)
        out[p + "mean_sec"] = np.where(h["sec_n"] > 0, _shrink(h["sec_sum"], h["sec_n"], fallback, k / 4), np.nan)

    # Face-à-face, orienté vers le joueur 1 du match courant.
    def _pair(frame):
        k1, k2 = frame["p1_key"].astype(str), frame["p2_key"].astype(str)
        a = k1.where(k1 <= k2, k2)
        return k1, a, a + "|" + k1.where(k1 > k2, k2)

    _, a_hist, pair_hist = _pair(matches)
    k1, a, pair = _pair(m)
    pc = contrib.assign(_a=contrib["game_id"].map(dict(zip(matches["game_id"], a_hist))))
    # Côté « a » de chaque match uniquement ; un match miroir (même combattant des deux côtés)
    # ne doit compter qu'une fois.
    pc = pc[pc["fighter"].astype(str) == pc["_a"]].drop_duplicates("game_id")
    pc["pair"] = pc["game_id"].map(dict(zip(matches["game_id"], pair_hist)))
    h2h = asof_cumulative(pc, "pair", m.assign(pair=pair), "pair", ["played", "won"])
    p1_is_a = (k1 == a).to_numpy()
    p1_won = np.where(p1_is_a, h2h["won"], h2h["played"] - h2h["won"])
    out["h2h_rounds_n"] = h2h["played"]
    out["h2h_p1_rate"] = _shrink(p1_won, h2h["played"], 0.5, k / 2)

    # Variantes exploratoires (plan, section 4.5) : séries récentes de la ligue.
    for hours in series_hours:
        past = asof_cumulative(contrib, "league_id", m, "league_id", ["won", "sec_sum", "sec_n"] + [f"perf_{c}" for c in ("R", "F")],
                               at=m["date_start"] - pd.Timedelta(hours=hours))
        n = (league["won"] - past["won"]).to_numpy()
        for c in ("R", "F"):
            out[f"serie_{hours}h_{c}"] = _shrink((league[f"perf_{c}"] - past[f"perf_{c}"]).to_numpy(), n, prior[c], k)
        sn = (league["sec_n"] - past["sec_n"]).to_numpy()
        out[f"serie_{hours}h_sec"] = np.where(sn > 0, (league["sec_sum"] - past["sec_sum"]).to_numpy() / np.maximum(sn, 1), np.nan)

    out["hour"] = m["date_start"].dt.hour.to_numpy()
    out["weekday"] = m["date_start"].dt.weekday.to_numpy()
    return out


def round_features(matches: pd.DataFrame, rounds: pd.DataFrame, lag: pd.Timedelta = DEFAULT_LAG,
                   k: float = 20.0) -> pd.DataFrame:
    """Une ligne par manche : variables intra-match + variables du match, avec identités."""
    im = intra_match(rounds)
    mf = match_features(matches, rounds, lag=lag, k=k)
    meta = matches[["game_id", "league_id", "date_start", "p1_key", "p2_key"]]
    return im.merge(meta, on="game_id", how="inner").merge(mf, on="game_id", how="inner")


ROUND_STATE = ["round_no", "s1_before", "s2_before", "score_diff", "p1_match_point", "p2_match_point", "prev_winner_p1"]
FIGHTER_CATS = ["p1_key", "p2_key"]
WINNER_NUM = ROUND_STATE + [
    "p1_rounds_n", "p2_rounds_n", "p1_win_rate", "p2_win_rate", "p1_win_rate_7d", "p2_win_rate_7d",
    "p1_match_win_rate", "p2_match_win_rate", "h2h_rounds_n", "h2h_p1_rate",
]
FINISH_NUM = ROUND_STATE + [f"match_{c}_so_far" for c in FINISH_CLASSES] + [
    "p1_win_rate", "p2_win_rate", "h2h_p1_rate",
] + [f"p{s}_{kind}_{c}" for s in (1, 2) for kind in ("perf", "suff") for c in FINISH_CLASSES] + [
    f"league_prior_{c}" for c in FINISH_CLASSES]
DURATION_NUM = ["logit_p_market", "line", "round_no", "s1_before", "s2_before", "prev_seconds",
                "p1_mean_sec", "p2_mean_sec", "league_mean_sec", "p1_perf_F", "p2_perf_F"]
TIME_VARIANT = ["hour", "weekday"]
SERIES_VARIANT = [f"serie_{h}h_{c}" for h in (3, 6, 12) for c in ("R", "F")] + [f"serie_{h}h_sec" for h in (3, 6, 12)]
