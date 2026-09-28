"""Journal permanent des prédictions publiées et bilan quotidien de leur taux de réussite.

Chaque manche prédite en direct, une fois résolue, est ajoutée à ``predictions_log.jsonl``
(volume ``ml_artifacts``) avec ce que le modèle ET le marché annonçaient au moment de la
publication. Le bilan quotidien compare les deux sur les mêmes manches : un taux de réussite
seul ne veut rien dire (le favori du marché gagne déjà ≈ 60 % des manches).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import rules
from .targets import FINISH_CLASSES, LEAGUE_NAMES, MK3, MKX

EPS = 1e-6


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # ligne tronquée (arrêt brutal pendant l'écriture) : ignorée
    return out


def _ll_binary(p, y) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -math.log(p if y else 1 - p)


def _block(n, ok_model, ok_market, ll_model, ll_market) -> dict:
    return {"n": n, "modele": ok_model / n if n else None, "marche": ok_market / n if n else None,
            "ll_modele": ll_model / n if n else None, "ll_marche": ll_market / n if n else None}


def summarize(records: list[dict]) -> dict:
    """Taux de réussite du modèle et du favori du marché, sur les mêmes manches, par cible."""
    out: dict = {}
    for league in (MKX, MK3):
        rows = [r for r in records if r["league"] == league and r.get("winner") in (1, 2) and r.get("mkt_p1") is not None]
        acc = [0, 0, 0.0, 0.0]
        for r in rows:
            y = r["winner"] == 1
            acc[0] += (r["p1"] >= 0.5) == y
            acc[1] += (r["mkt_p1"] >= 0.5) == y
            acc[2] += _ll_binary(r["p1"], y)
            acc[3] += _ll_binary(r["mkt_p1"], y)
        out[f"vainqueur_{league}"] = _block(len(rows), *acc)
    rows = [r for r in records if r.get("P_finish") and r.get("mkt_P_finish") and r.get("finish_real")]
    acc = [0, 0, 0.0, 0.0]
    for r in rows:
        k = FINISH_CLASSES.index(r["finish_real"])
        acc[0] += int(np.argmax(r["P_finish"])) == k
        acc[1] += int(np.argmax(r["mkt_P_finish"])) == k
        acc[2] += -math.log(max(r["P_finish"][k], EPS))
        acc[3] += -math.log(max(r["mkt_P_finish"][k], EPS))
    out[f"finish_{MK3}"] = _block(len(rows), *acc)
    rows = [r for r in records if r.get("p_over") is not None and r.get("mkt_over") is not None
            and r.get("seconds_real") is not None]
    acc = [0, 0, 0.0, 0.0]
    for r in rows:
        y = r["seconds_real"] > r["dur_line"]
        acc[0] += (r["p_over"] >= 0.5) == y
        acc[1] += (r["mkt_over"] >= 0.5) == y
        acc[2] += _ll_binary(r["p_over"], y)
        acc[3] += _ll_binary(r["mkt_over"], y)
    out[f"duree_{MKX}"] = _block(len(rows), *acc)
    return out


def rule_bets(records: list[dict]) -> list[dict]:
    """Paris fictifs de la règle prudente sur les manches jugées (à partir de ``JUDGED_FROM``)."""
    bets = []
    for r in records:
        if pd.Timestamp(r["ts"]) < rules.JUDGED_FROM or not r.get("finish_real"):
            continue
        choice = rules.pick(r.get("P_finish"), r.get("mkt_P_finish"), r.get("odds_finish"))
        if choice is None:
            continue
        k, o = choice
        won = FINISH_CLASSES[k] == r["finish_real"]
        bets.append({"game_id": r["game_id"], "classe": FINISH_CLASSES[k], "cote": o,
                     "profit": rules.STAKE * (o - 1) if won else -rules.STAKE})
    return bets


def _line(label: str, b: dict, unit: str = "manches") -> str:
    if not b["n"]:
        return f"{label} : aucune {unit[:-1]} résolue"
    better = "✅" if b["ll_modele"] < b["ll_marche"] else "➖"
    return (f"{label} ({b['n']} {unit}) : modèle {b['modele'] * 100:.1f} % juste, favori du marché "
            f"{b['marche'] * 100:.1f} % — qualité des probabilités {better} "
            f"(log-loss {b['ll_modele']:.3f} contre {b['ll_marche']:.3f})")


def format_daily(day: str, summary: dict, day_bets: list[dict], cumulative: dict) -> str:
    lines = [f"📈 BILAN DES PRÉDICTIONS — {pd.Timestamp(day).strftime('%d/%m/%Y')} (UTC)", ""]
    lines.append(_line(f"🥊 Vainqueur {LEAGUE_NAMES[MKX]}", summary[f"vainqueur_{MKX}"]))
    lines.append(_line(f"🥊 Vainqueur {LEAGUE_NAMES[MK3]}", summary[f"vainqueur_{MK3}"]))
    lines.append(_line(f"💀 Finish {LEAGUE_NAMES[MK3]}", summary[f"finish_{MK3}"]))
    lines.append(_line(f"⏱️ Durée {LEAGUE_NAMES[MKX]}", summary[f"duree_{MKX}"]))
    lines.append("")
    profit = sum(b["profit"] for b in day_bets)
    lines.append(f"🧪 Règle prudente (finish R/F/B, avantage ≥ {rules.MIN_EDGE * 100:.0f} pts, mise fictive "
                 f"{rules.STAKE:,.0f} F) : {len(day_bets)} paris ce jour, {profit:+,.0f} F".replace(",", " "))
    if cumulative["n_paris"]:
        lines.append(f"   Cumul depuis le {rules.JUDGED_FROM.strftime('%d/%m/%Y')} : {cumulative['n_paris']} paris, "
                     f"{cumulative['resultat']:+,.0f} F, rendement {cumulative['rendement'] * 100:+.1f} % "
                     f"[{cumulative['ic_bas'] * 100:+.1f} % ; {cumulative['ic_haut'] * 100:+.1f} %] — "
                     f"{cumulative['verdict']}".replace(",", " "))
    else:
        lines.append(f"   Jugée à partir du {rules.JUDGED_FROM.strftime('%d/%m/%Y')} : {cumulative['verdict']}.")
    lines += ["", "⚠️ Paris fictifs, à titre d'étude : aucun avantage de pari démontré à ce jour — ne pas parier."]
    return "\n".join(lines)


def daily_report(records: list[dict], day: str) -> str | None:
    """Texte du bilan d'une journée UTC (``AAAA-MM-JJ``), ``None`` si aucune manche ce jour-là."""
    day_records = [r for r in records if r["ts"][:10] == day]
    if not day_records:
        return None
    cumulative = rules.judge(rule_bets([r for r in records if r["ts"][:10] <= day]))
    return format_daily(day, summarize(day_records), rule_bets(day_records), cumulative)
