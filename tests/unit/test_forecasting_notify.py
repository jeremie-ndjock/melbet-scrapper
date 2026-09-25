"""Bilan des évaluations envoyé sur le salon Telegram de prédiction (expéditeur simulé :
aucun test n'écrit sur un vrai salon)."""
from __future__ import annotations

from forecasting import notify
from forecasting.report import AUCUN, AVANTAGE, PROMETTEUR

RUN = {"as_of": "2026-09-25T15:00:00+00:00", "resultats": [
    {"titre": "Vainqueur de manche — Mortal Kombat X", "cible": "vainqueur", "verdict": AUCUN,
     "backtest": {"n_paris": 0}},
    {"titre": "Durée de manche — Mortal Kombat X (P(durée > ligne principale))", "cible": "duree",
     "verdict": PROMETTEUR, "preliminaire": True, "backtest": {"n_paris": 104, "rendement": -0.038}},
    {"titre": "Type de finish — Mortal Kombat 3", "cible": "finish", "verdict": "Non évaluable"},
]}


class FakeSender:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    async def send(self, chat_id, text):
        self.calls.append((chat_id, text))
        return 1 if self.ok else None


def test_summary_lists_verdicts_and_warns_when_no_edge():
    text = notify.format_summary(RUN)
    assert text.startswith("📊 Évaluation des modèles — données jusqu'au 25/09/2026 15:00 UTC")
    assert "🥊 Vainqueur de manche — Mortal Kombat X : Aucun avantage détecté" in text
    assert "⏱️ Durée de manche — Mortal Kombat X (préliminaire)" in text and "104 paris, rendement -3.8 %" in text
    assert "aucun pari" in text
    assert "ne pas parier sur la base de ces modèles" in text


def test_summary_announces_a_demonstrated_edge():
    run = {**RUN, "resultats": [{**RUN["resultats"][0], "verdict": AVANTAGE}]}
    assert "✅" in notify.format_summary(run)


async def test_send_summary_uses_the_configured_chat(monkeypatch):
    monkeypatch.setenv(notify.CHAT_ENV, "-5371276826")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    sender = FakeSender()
    assert await notify.send_summary(RUN, sender=sender) is True
    assert sender.calls[0][0] == "-5371276826"
    assert await notify.send_summary(RUN, sender=FakeSender(ok=False)) is False


async def test_send_summary_is_skipped_when_not_configured(monkeypatch):
    monkeypatch.delenv(notify.CHAT_ENV, raising=False)
    sender = FakeSender()
    assert await notify.send_summary(RUN, sender=sender) is False
    assert sender.calls == []
