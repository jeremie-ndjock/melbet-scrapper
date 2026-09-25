"""Rapport d'évaluation (Markdown lisible + JSON exact) et échelle de verdict."""
from __future__ import annotations

import json
import math
from pathlib import Path

AVANTAGE = "Avantage démontré"
PROMETTEUR = "Signal prometteur, non démontré"
AUCUN = "Aucun avantage détecté"
NON_EVALUABLE = "Non évaluable"


def verdict(delta: dict, backtest: dict, data_ok: bool, label_ok: bool) -> str:
    """``delta`` : différence de log-loss (combinaison − marché seul recalibré), IC déjà ajusté
    pour le nombre d'essais K. Un avantage n'est « démontré » que si la log-loss baisse de façon
    significative ET si le backtest est positif avec un échantillon suffisant."""
    if not data_ok or not label_ok:
        return NON_EVALUABLE
    if (delta["ic_haut"] < 0 and backtest.get("ic_bas") is not None and backtest["ic_bas"] > 0
            and not backtest["echantillon_insuffisant"]):
        return AVANTAGE
    if delta["moyenne"] < 0:
        return PROMETTEUR
    return AUCUN


def _round(obj, nd: int = 6):
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v, nd) for v in obj]
    return obj


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:+.1f} %"


def _f(x, nd: int = 4) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _fcfa(x) -> str:
    return f"{x:+,.0f} F".replace(",", " ")


def render_markdown(run: dict) -> str:
    lines = [
        "# Rapport d'évaluation des modèles prédictifs",
        "",
        f"- Date de référence (`as_of`) : {run['as_of']}",
        f"- Version du code : `{run['git_commit']}`",
        f"- Délai de disponibilité d'un match : {run['lag_minutes']:.0f} min",
        "",
        "Rappel : le seul critère de succès est d'apporter une information que la cote du bookmaker "
        "n'a pas déjà (plan d'entraînement, sections 7.3 et 8). « Combinaison » = modèle combiné au "
        "marché ; « marché recalibré » = le marché seul, recalibré sur la même période : c'est la "
        "référence du verdict.",
        "",
    ]
    for res in run["resultats"]:
        lines += [f"## {res['titre']}", ""]
        if res.get("preliminaire"):
            lines += ["**PRÉLIMINAIRE (≈ 3 jours de données)** — aucun chiffre ci-dessous ne permet encore de conclure.", ""]
        lines += [f"**Verdict : {res['verdict']}**", ""]
        if res.get("erreur"):
            lines += [f"Évaluation impossible : {res['erreur']}", ""]
            continue
        lines += [
            f"- Manches évaluées (test, avec cotes) : {res['n_test']} ; entraînement : {res.get('n_train', '—')}",
            f"- Modèle retenu (sur la validation) : {res.get('modele_retenu', '—')}",
            f"- Poids du modèle dans la combinaison : {_f(res['poids_modele'], 3)} "
            f"(IC 95 % [{_f(res['poids_modele_ic'][0], 3)} ; {_f(res['poids_modele_ic'][1], 3)}]) — "
            "un poids nul signifie que le modèle n'apporte rien au marché",
            "",
            "| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |",
            "|---|---|---|---|---|---|",
        ]
        for name, m in res["scores"].items():
            lines.append(f"| {name} | {_f(m['log_loss'])} | {_f(m['brier'])} | {_f(m['ece_quantile'])} | "
                         f"{_f(m['ece_uniforme'])} | {m['intervalles_non_vides'] * 100:.0f} % |")
        d, dr = res["delta"], res["delta_marche_brut"]
        lines += [
            "",
            f"- Écart de log-loss combinaison − marché recalibré : {d['moyenne']:+.5f} "
            f"(IC ajusté pour K={res['k']} essai(s) : [{d['ic_bas']:+.5f} ; {d['ic_haut']:+.5f}])",
            f"- Écart combinaison − marché brut : {dr['moyenne']:+.5f} [{dr['ic_bas']:+.5f} ; {dr['ic_haut']:+.5f}]",
            f"- Contrôle des étiquettes face au marché : {'réussi' if res['controle_etiquettes']['ok'] else 'ÉCHEC'}",
            "",
        ]
        b = res["backtest"]
        lines += [
            f"**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = {res['tau']:.2f} choisi sur la validation) : "
            f"{b['n_paris']} paris, résultat {_fcfa(b['resultat'])}, rendement {_pct(b['rendement'])} "
            f"[{_pct(b['ic_bas'])} ; {_pct(b['ic_haut'])}]",
        ]
        if b["echantillon_insuffisant"]:
            need = b["n_requis"]
            lines.append(f"Échantillon insuffisant pour conclure" + (f" (il faudrait ≈ {need} paris)." if need else "."))
        if b.get("par_issue"):
            lines += ["", "| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |", "|---|---|---|---|---|"]
            for label, d in b["par_issue"].items():
                lines.append(f"| {label} | {d['n_paris']} | {_fcfa(d['resultat'])} | {_pct(d['rendement'])} | {d['cote_moyenne']:.2f} |")
        if res.get("variabilite_marche"):
            lines += ["", "Variabilité du marché (écart-type de sa probabilité, par issue) : "
                      + ", ".join(f"{v:.3f}" for v in res["variabilite_marche"])
                      + " — une valeur proche de 0 signifie une cote quasiment figée."]
        if res.get("validation_glissante"):
            lines += ["", "**Validation glissante sur l'historique (sans cotes)** — log-loss par semaine :", "",
                      "| Semaine | n | Fréquences de base | Modèle | + heure | + séries récentes |", "|---|---|---|---|---|---|"]
            for w in res["validation_glissante"]:
                lines.append(f"| {w['semaine'][:10]} | {w['n']} | {_f(w['base'])} | {_f(w['modele'])} | {_f(w['heure'])} | {_f(w['series'])} |")
        if res.get("notes"):
            lines += [""] + [f"- {n}" for n in res["notes"]]
        lines.append("")
    q = run.get("qualite", {})
    if q:
        lines += ["## Qualité des données", "", "```", json.dumps(_round(q), ensure_ascii=False, indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)


def write_report(out_dir: Path, run: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "resultats.json").write_text(json.dumps(_round(run), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "rapport.md").write_text(render_markdown(run), encoding="utf-8")
    return out_dir
