"""Évaluation des trois cibles, sur des DataFrames déjà extraits (aucune entrée/sortie ici).

Cibles « vainqueur de manche » et « type de finish » : 3 mois d'étiquettes sans cotes, ≈ 3 jours
avec cotes. Le modèle apprend sur l'historique, se calibre sur les 7 jours précédant l'arrivée
des cotes, puis est confronté au marché sur la période avec cotes (un tiers pour ajuster la
combinaison modèle + marché et le seuil de pari, deux tiers pour les chiffres finaux).

Cible « durée de manche » : environ 3 jours de données au total, tous avec cotes ; blocs de
matchs consécutifs réévalués au fil de l'eau, verdict toujours préliminaire.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import logit

from . import backtest as bt
from .features import (DEFAULT_LAG, DURATION_NUM, FIGHTER_CATS, FINISH_NUM, SERIES_VARIANT, TIME_VARIANT,
                       WINNER_NUM, round_features)
from .market import closing_quotes, devig, devig_power, main_line
from .metrics import as_matrix, brier, classwise_ece, log_loss, paired_bootstrap, row_log_loss
from .models import LGBMModel, LogLinearPool, PlattCalibrator, TemperatureCalibrator, make_logreg, pool_weight_ci
from .report import NON_EVALUABLE, verdict
from .split import before, between, match_blocks, split_by_match_order, weekly_folds
from .targets import (FINISH_CLASSES, FINISH_INDEX, FINISH_MARKET_T_ORDERED, G_DURATION, G_FINISH, G_WINNER,
                      LEAGUE_NAMES, MK3, MKX, T_OVER, T_P1, T_P2, T_UNDER)


@dataclass
class Config:
    lag: pd.Timedelta = DEFAULT_LAG
    valid_days: int = 7
    embargo: pd.Timedelta = pd.Timedelta(hours=1)
    block_embargo: pd.Timedelta = pd.Timedelta(minutes=30)
    seed: int = 0
    n_boot: int = 2000
    walk_folds: int = 6
    walk_step: pd.Timedelta = pd.Timedelta(days=7)
    walk_train_days: int = 45
    use_lgbm: bool = True
    min_test_rows: int = 200


# --------------------------------------------------------------------------- outils communs

def _full_proba(model, X: pd.DataFrame, n_classes: int) -> np.ndarray:
    """Probabilités sur toutes les classes, même si une classe manquait à l'entraînement."""
    P = model.predict_proba(X)
    full = np.full((len(X), n_classes), 1e-6)
    for j, c in enumerate(model.classes_):
        full[:, int(c)] = P[:, j]
    return full / full.sum(axis=1, keepdims=True)


def _calibrate(P_fit, y_fit, binary: bool):
    return PlattCalibrator().fit(P_fit[:, 1], y_fit) if binary else TemperatureCalibrator().fit(P_fit, y_fit)


def _apply(cal, P, binary: bool) -> np.ndarray:
    return as_matrix(cal.transform(P[:, 1])) if binary else cal.transform(P)


def _scores(y, P, binary: bool) -> dict:
    p = P[:, 1] if binary else P
    q, u = classwise_ece(p, y, strategy="quantile"), classwise_ece(p, y, strategy="uniform")
    return {"log_loss": log_loss(y, p), "brier": brier(y, p), "ece_quantile": q["ece"],
            "ece_uniforme": u["ece"], "intervalles_non_vides": u["nonempty_frac"], "n": int(len(y))}


def label_check(y, P_market) -> dict:
    """Les issues observées doivent être cohérentes avec ce que le marché anticipe. Un écart
    massif signale une étiquette qui ne correspond pas à ce que le marché règle (ex. durée mal
    mesurée) : le verdict devient alors « Non évaluable »."""
    y = np.asarray(y, int)
    n, ok, rows = len(y), True, []
    for k in range(P_market.shape[1]):
        freq, mp = float((y == k).mean()), float(P_market[:, k].mean())
        z = (freq - mp) / math.sqrt(max(mp * (1 - mp), 1e-9) / n)
        bad = abs(freq - mp) > 0.03 and abs(z) > 4
        ok &= not bad
        rows.append({"classe": k, "frequence": freq, "p_marche": mp, "z": z})
    return {"ok": bool(ok), "classes": rows}


def _leakage_guard(market: pd.DataFrame, round_ends: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Écarte toute cote de clôture postérieure à la fin de sa manche (compté ; attendu : 0)."""
    if market.empty or round_ends.empty:
        return market, 0
    m = market.merge(round_ends, on=["game_id", "round_no"], how="left")
    late = m["round_end"].notna() & (m["ts_close"] >= m["round_end"])
    return m[~late].drop(columns="round_end"), int(late.sum())


def _select_model(feat, y, num, cats, n_classes, train, calib, select, cfg):
    """Entraîne régression logistique et LightGBM, calibre chacun sur la 1re moitié de la
    validation, choisit sur la 2e moitié par la log-loss, avec un garde-fou de calibration."""
    binary = n_classes == 2
    cols = num + cats
    cands = {"régression logistique": make_logreg(num, cats).fit(feat.loc[train, cols], y[train])}
    if cfg.use_lgbm:
        cands["LightGBM"] = LGBMModel(num, cats, n_classes, cfg.seed).fit(feat.loc[train, cols], y[train])
    fitted, scores = {}, {}
    for name, model in cands.items():
        cal = _calibrate(_full_proba(model, feat.loc[calib, cols], n_classes), y[calib], binary)
        P = _apply(cal, _full_proba(model, feat.loc[select, cols], n_classes), binary)
        fitted[name], scores[name] = (model, cal), _scores(y[select], P, binary)
    ranked = sorted(scores, key=lambda n: scores[n]["log_loss"])
    best = ranked[0]
    if len(ranked) > 1:
        other = ranked[1]
        if scores[best]["ece_quantile"] > 1.5 * scores[other]["ece_quantile"] + 0.005:
            best = other  # meilleure log-loss mais nettement moins bien calibré
    return best, fitted[best][0], fitted[best][1], scores


def _base_rate(y_train, n_classes: int, n_rows: int) -> np.ndarray:
    freq = np.bincount(np.asarray(y_train, int), minlength=n_classes) / max(len(y_train), 1)
    return np.tile(np.clip(freq, 1e-6, 1), (n_rows, 1))


def _walk_forward(feat, y, num, cats, n_classes, end, cfg) -> list[dict]:
    """Validation glissante hebdomadaire sur l'historique (sans cotes) : stabilité, dérive, et
    apport des variantes « heure » et « séries récentes » (plan, section 4.5)."""
    ds = feat["date_start"]
    series = [c for c in SERIES_VARIANT if not c.endswith("_sec")]
    rows = []
    folds = weekly_folds(ds, end - cfg.walk_folds * cfg.walk_step, end, cfg.lag, cfg.embargo, step=cfg.walk_step)
    for label, train, test in folds:
        train = train & (ds >= pd.Timestamp(label) - pd.Timedelta(days=cfg.walk_train_days))
        if test.sum() < cfg.min_test_rows or train.sum() < 5 * cfg.min_test_rows:
            continue
        yt = y[test]
        row = {"semaine": label, "n": int(test.sum()),
               "base": log_loss(yt, _base_rate(y[train], n_classes, int(test.sum())))}
        for key, extra in (("modele", []), ("heure", TIME_VARIANT), ("series", series)):
            model = make_logreg(num + extra, cats).fit(feat.loc[train, num + extra + cats], y[train])
            row[key] = log_loss(yt, _full_proba(model, feat.loc[test, num + extra + cats], n_classes))
        rows.append(row)
    return rows


def _market_frame(quotes: pd.DataFrame, odds_cols: list[str]) -> pd.DataFrame:
    """Colonnes utiles des cotes de clôture ; un tableau vide (aucune cote) garde ses colonnes."""
    return quotes.reindex(columns=["game_id", "round_no", "ts_close"] + odds_cols)


# --------------------------------------------------------------------------- cibles B et C

def evaluate_history_target(*, title: str, target: str, league_id: int, feat: pd.DataFrame, y_col: str,
                            num: list[str], cats: list[str], n_classes: int, market: pd.DataFrame,
                            odds_cols: list[str], w_start: pd.Timestamp, as_of: pd.Timestamp, cfg: Config,
                            registry, meta: dict, notes: list[str] | None = None, store: dict | None = None,
                            labels: list[str] | None = None) -> dict:
    binary = n_classes == 2
    feat = feat[feat[y_col].notna()].sort_values(["date_start", "game_id", "round_no"]).reset_index(drop=True)
    y = feat[y_col].astype(int).to_numpy()
    ds = feat["date_start"]
    res = {"titre": title, "cible": target, "ligue": league_id, "notes": list(notes or [])}

    valid_start = w_start - pd.Timedelta(days=cfg.valid_days)
    train = before(ds, valid_start, cfg.lag).to_numpy()
    valid = between(ds, valid_start, w_start, cfg.lag)
    calib, select = split_by_match_order(feat.loc[valid, "game_id"], ds[valid], 0.5, pd.Timedelta(0))
    calib_m = valid.copy()
    calib_m[valid] = calib.to_numpy()
    select_m = valid.copy()
    select_m[valid] = select.to_numpy()
    calib_m, select_m = calib_m.to_numpy(), select_m.to_numpy()

    name, model, cal, val_scores = _select_model(feat, y, num, cats, n_classes, train, calib_m, select_m, cfg)
    if store is not None:
        store[f"{target}_{league_id}"] = {"nom": name, "modele": model, "calibrateur": cal, "variables": num + cats}
    res.update(modele_retenu=name, scores_validation=val_scores, n_train=int(train.sum()),
               base_validation=log_loss(y[select_m], _base_rate(y[train], n_classes, int(select_m.sum()))))

    w = feat[(ds >= w_start) & (ds + cfg.lag <= as_of)].merge(market, on=["game_id", "round_no"], how="inner")
    w = w.sort_values(["date_start", "game_id", "round_no"]).reset_index(drop=True)
    if len(w) < cfg.min_test_rows:
        return {**res, "verdict": NON_EVALUABLE, "erreur": f"trop peu de manches avec cotes ({len(w)})"}
    odds = w[odds_cols].to_numpy(float)
    P_fair, margin = devig(odds)
    P_model = _apply(cal, _full_proba(model, w[num + cats], n_classes), binary)
    yw = w[y_col].astype(int).to_numpy()
    v_mask, t_mask = split_by_match_order(w["game_id"], w["date_start"], 1 / 3, cfg.embargo)
    v, t = v_mask.to_numpy(), t_mask.to_numpy()

    pm = (lambda P: P[:, 1]) if binary else (lambda P: P)
    full = LogLinearPool(binary).fit(pm(P_fair[v]), pm(P_model[v]), yw[v])
    mo = LogLinearPool(binary, use_model=False).fit(pm(P_fair[v]), None, yw[v])
    P_full = as_matrix(full.predict(pm(P_fair), pm(P_model))) if binary else full.predict(P_fair, P_model)
    P_mo = as_matrix(mo.predict(pm(P_fair))) if binary else mo.predict(P_fair)
    w_ci = pool_weight_ci(pm(P_fair[v]), pm(P_model[v]), yw[v], w.loc[v, "game_id"], binary, seed=cfg.seed)

    rec = {"as_of": meta["as_of"], "git_commit": meta["git_commit"], "cible": target, "ligue": league_id, "variante": "base"}
    k = registry.k_for(rec)
    alpha = 0.05 / k
    yt, gt = yw[t], w.loc[t, "game_id"].to_numpy()
    delta = paired_bootstrap(row_log_loss(yt, pm(P_full[t])), row_log_loss(yt, pm(P_mo[t])), gt, alpha, cfg.n_boot, cfg.seed)
    delta_raw = paired_bootstrap(row_log_loss(yt, pm(P_full[t])), row_log_loss(yt, pm(P_fair[t])), gt, alpha, cfg.n_boot, cfg.seed)
    tau = bt.choose_tau(P_full[v], P_fair[v], odds[v], yw[v])
    bets = bt.value_bets(P_full[t], P_fair[t], odds[t], yt, tau)
    summary = bt.summarize(bets, gt, cfg.n_boot, cfg.seed)
    summary["par_issue"] = bt.by_outcome(bets, labels or [str(i) for i in range(n_classes)])
    check = label_check(yt, P_fair[t])
    extra_scores = {"Fréquences de base (historique)": _scores(yt, _base_rate(y[train], n_classes, len(yt)), binary)}
    if not binary:
        # Le retrait multiplicatif de la marge flatte les issues rares : contrôle par la méthode
        # « power » pour ne pas attribuer au modèle un avantage qui viendrait de la méthode.
        extra_scores["Marché brut (méthode power)"] = _scores(yt, devig_power(odds[t]), binary)

    res.update(
        n_test=int(t.sum()), n_calage=int(v.sum()), marge_moyenne=float(margin.mean()), k=k,
        poids_modele=full.model_weight, poids_modele_ic=list(w_ci), tau=tau, backtest=summary,
        controle_etiquettes=check, delta=delta, delta_marche_brut=delta_raw,
        variabilite_marche=[float(x) for x in P_fair[t].std(axis=0)],
        scores={
            **extra_scores,
            "Marché brut (marge retirée)": _scores(yt, P_fair[t], binary),
            "Marché recalibré": _scores(yt, P_mo[t], binary),
            "Modèle seul": _scores(yt, P_model[t], binary),
            "Combinaison modèle + marché": _scores(yt, P_full[t], binary),
        },
        validation_glissante=_walk_forward(feat, y, num, cats, n_classes, valid_start, cfg),
    )
    res["verdict"] = verdict(delta, summary, data_ok=True, label_ok=check["ok"])
    registry.register({**rec, "verdict": res["verdict"], "delta": delta, "n_test": res["n_test"],
                       "backtest_rendement": summary["rendement"], "n_paris": summary["n_paris"]})
    return res


# --------------------------------------------------------------------------- cible A

def evaluate_duration(*, feat: pd.DataFrame, lines: pd.DataFrame, as_of: pd.Timestamp, cfg: Config,
                      registry, meta: dict, n_blocks: int = 6) -> dict:
    res = {"titre": "Durée de manche — Mortal Kombat X (P(durée > ligne principale))", "cible": "duree",
           "ligue": MKX, "preliminaire": True, "notes": []}
    df = feat[feat["seconds"].notna() & (feat["date_start"] + cfg.lag <= as_of)].merge(
        lines[["game_id", "round_no", "line", f"close_{T_UNDER}", f"close_{T_OVER}"]], on=["game_id", "round_no"])
    df = df.sort_values(["date_start", "game_id", "round_no"]).reset_index(drop=True)
    if len(df) < 6 * cfg.min_test_rows // 2:
        return {**res, "verdict": NON_EVALUABLE, "erreur": f"trop peu de manches avec durée et cote ({len(df)})"}
    odds = df[[f"close_{T_UNDER}", f"close_{T_OVER}"]].to_numpy(float)
    P_fair, margin = devig(odds)
    df["logit_p_market"] = logit(np.clip(P_fair[:, 1], 1e-6, 1 - 1e-6))
    y = (df["seconds"] > df["line"]).astype(int).to_numpy()
    blocks, starts = match_blocks(df["game_id"], df["date_start"], n_blocks)
    blocks = blocks.to_numpy()
    cats = ["prev_finish"]

    def fold(b):
        tr = (blocks < b) & (df["date_start"] + cfg.lag <= starts[b]).to_numpy()
        te = (blocks == b) & (df["date_start"] >= starts[b] + cfg.block_embargo).to_numpy()
        full = make_logreg(DURATION_NUM, cats).fit(df.loc[tr, DURATION_NUM + cats], y[tr])
        mo = make_logreg(["logit_p_market"], []).fit(df.loc[tr, ["logit_p_market"]], y[tr])
        return te, full.predict_proba(df.loc[te, DURATION_NUM + cats])[:, 1], mo.predict_proba(df.loc[te, ["logit_p_market"]])[:, 1]

    te_v, pf_v, _ = fold(2)
    tau = bt.choose_tau(as_matrix(pf_v), P_fair[te_v], odds[te_v], y[te_v])
    idx, p_full, p_mo = [], [], []
    for b in range(3, n_blocks):
        te, pf, pmo = fold(b)
        idx.append(np.flatnonzero(te))
        p_full.append(pf)
        p_mo.append(pmo)
    idx, p_full, p_mo = np.concatenate(idx), np.concatenate(p_full), np.concatenate(p_mo)
    yt, gt = y[idx], df.loc[idx, "game_id"].to_numpy()

    rec = {"as_of": meta["as_of"], "git_commit": meta["git_commit"], "cible": "duree", "ligue": MKX, "variante": "base"}
    k = registry.k_for(rec)
    alpha = 0.05 / k
    delta = paired_bootstrap(row_log_loss(yt, p_full), row_log_loss(yt, p_mo), gt, alpha, cfg.n_boot, cfg.seed)
    delta_raw = paired_bootstrap(row_log_loss(yt, p_full), row_log_loss(yt, P_fair[idx, 1]), gt, alpha, cfg.n_boot, cfg.seed)
    bets = bt.value_bets(as_matrix(p_full), P_fair[idx], odds[idx], yt, tau)
    summary = bt.summarize(bets, gt, cfg.n_boot, cfg.seed)
    summary["par_issue"] = bt.by_outcome(bets, ["moins", "plus"])
    check = label_check(yt, P_fair[idx])
    res.update(
        n_test=int(len(idx)), n_train=int((blocks < 3).sum()), modele_retenu="régression logistique (variables fixées à l'avance)",
        marge_moyenne=float(margin.mean()), k=k, poids_modele=None, poids_modele_ic=[None, None], tau=tau,
        backtest=summary, controle_etiquettes=check, delta=delta, delta_marche_brut=delta_raw,
        scores={
            "Marché brut (marge retirée)": _scores(yt, P_fair[idx], True),
            "Marché recalibré": _scores(yt, as_matrix(p_mo), True),
            "Modèle (inclut la cote du marché)": _scores(yt, as_matrix(p_full), True),
        },
    )
    res["notes"].append("Ici le modèle inclut directement la cote du marché parmi ses variables : il est comparé au "
                        "marché seul recalibré (même protocole), le poids de combinaison est donc sans objet.")
    res["verdict"] = verdict(delta, summary, data_ok=True, label_ok=check["ok"])
    registry.register({**rec, "verdict": res["verdict"], "delta": delta, "n_test": res["n_test"],
                       "backtest_rendement": summary["rendement"], "n_paris": summary["n_paris"]})
    return res


# --------------------------------------------------------------------------- orchestration

REQUIRED_T = {G_WINNER: (T_P2, T_P1), G_DURATION: (T_UNDER, T_OVER), G_FINISH: FINISH_MARKET_T_ORDERED}


def run_all(data: dict, *, as_of: pd.Timestamp, cfg: Config, registry, meta: dict, targets: set[str],
            store: dict | None = None) -> list[dict]:
    """``data[league]`` : matches, rounds (nettoyés), odds, round_ends, w_start."""
    results = []
    for league in (MKX, MK3):
        d = data.get(league)
        if d is None:
            continue
        feat = round_features(d["matches"], d["rounds"], lag=cfg.lag)
        feat["y_winner"] = (feat["winner"] == 1).astype(float)
        feat["y_finish"] = feat["finish"].map(FINISH_INDEX)
        quotes = closing_quotes(d["odds"], REQUIRED_T)

        if "winner" in targets:
            q = quotes[quotes["g"] == G_WINNER]
            market, leaked = _leakage_guard(_market_frame(q, [f"close_{T_P2}", f"close_{T_P1}"]), d["round_ends"])
            results.append(evaluate_history_target(
                title=f"Vainqueur de manche — {LEAGUE_NAMES[league]}", target="vainqueur", league_id=league,
                feat=feat, y_col="y_winner", num=WINNER_NUM, cats=FIGHTER_CATS, n_classes=2, market=market,
                odds_cols=[f"close_{T_P2}", f"close_{T_P1}"], labels=["joueur 2", "joueur 1"], w_start=d["w_start"], as_of=as_of, cfg=cfg,
                registry=registry, meta=meta, notes=[f"Cotes écartées car postérieures à la manche : {leaked}"], store=store))

        if "finish" in targets and league == MK3:
            cols = [f"close_{t}" for t in FINISH_MARKET_T_ORDERED]
            q = quotes[quotes["g"] == G_FINISH]
            market, leaked = _leakage_guard(_market_frame(q, cols), d["round_ends"])
            results.append(evaluate_history_target(
                title="Type de finish — Mortal Kombat 3 (7 classes)", target="finish", league_id=league,
                feat=feat, y_col="y_finish", num=FINISH_NUM, cats=FIGHTER_CATS + ["prev_finish"], n_classes=len(FINISH_CLASSES),
                market=market, odds_cols=cols, labels=list(FINISH_CLASSES), w_start=d["w_start"], as_of=as_of, cfg=cfg,
                registry=registry, meta=meta,
                notes=[f"Cotes écartées car postérieures à la manche : {leaked}",
                       "Aucune pondération des classes rares (elle fausserait les probabilités) : un rappel proche "
                       "de 0 sur Hara-Kiri ou Animality est attendu d'un modèle bien calibré.",
                       "Environ 0,15 % de Hara-Kiri : aucun chiffre par classe n'est significatif pour les classes rares."],
                store=store))

        if "duration" in targets and league == MKX:
            q = quotes[quotes["g"] == G_DURATION]
            lines = _market_frame(main_line(q, T_OVER, T_UNDER), ["line", f"close_{T_UNDER}", f"close_{T_OVER}"])
            lines, leaked = _leakage_guard(lines, d["round_ends"])
            res = evaluate_duration(feat=feat, lines=lines, as_of=as_of, cfg=cfg, registry=registry, meta=meta)
            res["notes"].append(f"Cotes écartées car postérieures à la manche : {leaked}")
            results.append(res)
    return results
