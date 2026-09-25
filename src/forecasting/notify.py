"""Bilan de chaque évaluation envoyé sur le salon Telegram « Prédiction Mortal Kombat ».

Décision de l'utilisateur (2026-09-25) : on publie les verdicts, jamais de prédiction manche par
manche tant qu'aucun modèle n'a démontré un avantage de pari — rien qui pousse à parier à perte.
Même principe que le fil de match : un échec d'envoi est journalisé, jamais bloquant.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from collector.telegram_feed import MatchFeedSender

from .report import AVANTAGE

log = logging.getLogger("forecasting.notify")
CHAT_ENV = "TELEGRAM_PREDICTION_CHAT_ID"
ICONS = {"vainqueur": "🥊", "finish": "💀", "duree": "⏱️"}


def _roi(backtest: dict) -> str:
    n = backtest.get("n_paris", 0)
    if not n:
        return "aucun pari"
    return f"{n} paris, rendement {backtest['rendement'] * 100:+.1f} %"


def format_summary(run: dict) -> str:
    as_of = pd.Timestamp(run["as_of"]).strftime("%d/%m/%Y %H:%M")
    lines = [f"📊 Évaluation des modèles — données jusqu'au {as_of} UTC", ""]
    for res in run["resultats"]:
        title = res["titre"].split(" (")[0] + (" (préliminaire)" if res.get("preliminaire") else "")
        lines.append(f"{ICONS.get(res['cible'], '•')} {title} : {res['verdict']}")
        if res.get("backtest"):
            lines.append(f"    backtest à mise fixe : {_roi(res['backtest'])}")
    lines.append("")
    if any(res["verdict"] == AVANTAGE for res in run["resultats"]):
        lines.append("✅ Au moins un modèle montre un avantage démontré (détail dans le rapport).")
    else:
        lines.append("⚠️ Aucun avantage de pari démontré : ne pas parier sur la base de ces modèles.")
    return "\n".join(lines)


async def send_summary(run: dict, sender: MatchFeedSender | None = None) -> bool:
    """Envoie le bilan si le salon est configuré. ``False`` si non configuré ou en cas d'échec."""
    chat_id, token = os.environ.get(CHAT_ENV), os.environ.get("TELEGRAM_BOT_TOKEN")
    if not chat_id or not (token or sender):
        log.info("salon Telegram de prédiction non configuré (%s) : bilan non envoyé", CHAT_ENV)
        return False
    sender = sender or MatchFeedSender(token)
    return await sender.send(chat_id, format_summary(run)) is not None
