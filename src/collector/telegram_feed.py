"""Fil de match en direct sur Telegram (fonctionnalité demandée le 2026-09-22, hors du plan initial
à 11 étapes) : un message par match, édité à chaque nouvelle manche plutôt que renvoyé.

Distinct de ``alerting.py`` par nature : les alertes sont rares, limitées (``ThrottledAlerter``,
au plus une par ``cooldown_seconds``) et destinées à signaler une panne. Ce fil est au contraire
volontairement fréquent (plusieurs mises à jour par match, des dizaines de matchs par jour) et
n'a rien d'un incident — le limiter serait donc une erreur. Utilise le même bot (``TELEGRAM_BOT_TOKEN``)
mais un salon dédié (``TELEGRAM_MATCH_CHAT_ID``), pour ne jamais mélanger ce flux avec les alertes
techniques critiques (voir Memoire.md, section 19).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("collector.telegram_feed")

API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class MatchFeedConfig:
    bot_token: str | None = None
    chat_id: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


def load_match_feed_config_from_env() -> MatchFeedConfig:
    return MatchFeedConfig(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        chat_id=os.environ.get("TELEGRAM_MATCH_CHAT_ID") or None,
    )


class MatchFeedSender:
    """N'importe quel échec réseau est journalisé et renvoie ``None``/``False`` — jamais levé : un
    problème d'envoi Telegram ne doit jamais interrompre la collecte des cotes (même principe que
    ``AlertSender``)."""

    def __init__(self, config: MatchFeedConfig, *, timeout: float = 10.0):
        self._config = config
        self._timeout = timeout

    async def send(self, text: str) -> int | None:
        """Envoie un nouveau message. Retourne son identifiant, ou ``None`` en cas d'échec."""
        url = f"{API_BASE}/bot{self._config.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={"chat_id": self._config.chat_id, "text": text})
                response.raise_for_status()
                return response.json()["result"]["message_id"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("échec d'envoi du fil de match : %s: %s", type(exc).__name__, exc)
            return None

    async def edit(self, message_id: int, text: str) -> bool:
        """Édite un message existant. ``False`` en cas d'échec (ex. message trop ancien pour être
        édité, ou supprimé manuellement) — l'appelant peut alors renvoyer un nouveau message."""
        url = f"{API_BASE}/bot{self._config.bot_token}/editMessageText"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={
                    "chat_id": self._config.chat_id, "message_id": message_id, "text": text,
                })
                response.raise_for_status()
                return True
        except httpx.HTTPError as exc:
            log.warning("échec d'édition du fil de match (message %s) : %s: %s", message_id, type(exc).__name__, exc)
            return False

    async def send_or_edit(self, message_id: int | None, text: str) -> int | None:
        """Édite le message existant s'il y en a un ; sinon (ou si l'édition échoue), en envoie un
        nouveau. Retourne l'identifiant du message à retenir pour la prochaine mise à jour."""
        if message_id is not None:
            if await self.edit(message_id, text):
                return message_id
        return await self.send(text)


def format_match_message(opp1_name: str, opp2_name: str, rounds, *, league_name: str,
                          match_no_of_day: int | None = None, finish_threshold: int = 5) -> str:
    """Construit le texte complet à partir de toutes les manches connues (reconstruit à chaque
    fois plutôt qu'ajouté incrémentalement : idempotent, se corrige tout seul si un cycle a été
    manqué, jamais de dérive possible entre le message affiché et l'état réel du match)."""
    en_tete = f"🎮 {league_name}"
    if match_no_of_day is not None:
        en_tete += f" — Match n°{match_no_of_day} de la journée"
    lines = [en_tete, f"🥊 {opp1_name} VS {opp2_name}", ""]
    tally1 = tally2 = 0
    for r in rounds:
        if r.winner_name == opp1_name:
            tally1 += 1
        elif r.winner_name == opp2_name:
            tally2 += 1
        else:
            log.warning("vainqueur de manche %r ne correspond à aucun des deux adversaires (%r / %r)",
                        r.winner_name, opp1_name, opp2_name)
        finish = r.finish_di or "?"
        temps = f"{r.seconds} secondes" if r.seconds is not None else "temps inconnu"
        lines.append(f"Manche {r.round_no} : vainqueur {r.winner_name}, temps: {temps}, "
                     f"Type de finishing: {finish} (score {tally1}-{tally2})")
    if tally1 >= finish_threshold or tally2 >= finish_threshold:
        vainqueur = opp1_name if tally1 > tally2 else opp2_name
        lines.append("")
        lines.append(f"🏆 Vainqueur du match : {vainqueur} ({tally1}-{tally2})")
    return "\n".join(lines)
