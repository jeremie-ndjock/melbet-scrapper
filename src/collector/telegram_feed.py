"""Fil de match en direct sur Telegram (fonctionnalité demandée le 2026-09-22, hors du plan initial
à 11 étapes) : un message par match, édité à chaque nouvelle manche plutôt que renvoyé, sur un
salon Telegram dédié **par ligue** (un salon « Mortal Kombat X », un salon « Mortal Kombat 3 »,
demandé le 2026-09-22 après la mise en service initiale sur un salon unique).

Distinct de ``alerting.py`` par nature : les alertes sont rares, limitées (``ThrottledAlerter``,
au plus une par ``cooldown_seconds``) et destinées à signaler une panne. Ce fil est au contraire
volontairement fréquent (plusieurs mises à jour par match, des dizaines de matchs par jour) et
n'a rien d'un incident — le limiter serait donc une erreur. Utilise le même bot
(``TELEGRAM_BOT_TOKEN``) pour tous les salons, mais un salon distinct par ligue
(``TELEGRAM_MATCH_CHAT_ID_<league_id>``), pour ne jamais mélanger ce flux avec les alertes
techniques critiques ni les deux ligues entre elles (voir Memoire.md, section 19).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("collector.telegram_feed")

API_BASE = "https://api.telegram.org"
CHAT_ID_ENV_PREFIX = "TELEGRAM_MATCH_CHAT_ID_"

# Emoji par type de finishing (Memoire.md, section 15). Un type absent de cette liste (nouveau
# type découvert plus tard) affiche un point d'interrogation plutôt que de planter.
FINISH_EMOJIS = {
    "Regular": "🥊", "Fatality": "💀", "Brutality": "🔥", "Babality": "👶",
    "Friendship": "🤝", "Animality": "🐾", "Hara-Kiri": "🗡️",
}


def _finish_emoji(finish_di: str | None) -> str:
    return FINISH_EMOJIS.get(finish_di or "", "❓")


@dataclass(frozen=True)
class MatchFeedConfig:
    bot_token: str | None = None
    # Un salon Telegram par ligue : {1252965: "-100...", 2282406: "-100..."}. Une ligue absente de
    # ce dictionnaire n'a simplement pas son fil (les autres continuent normalement) — pas besoin
    # de configurer les deux salons pour activer la fonctionnalité sur l'un d'eux.
    chat_ids: dict[int, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_ids)


def load_match_feed_config_from_env() -> MatchFeedConfig:
    chat_ids: dict[int, str] = {}
    for key, value in os.environ.items():
        if key.startswith(CHAT_ID_ENV_PREFIX) and value:
            suffix = key[len(CHAT_ID_ENV_PREFIX):]
            if suffix.isdigit() or (suffix.startswith("-") and suffix[1:].isdigit()):
                chat_ids[int(suffix)] = value
    return MatchFeedConfig(bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None, chat_ids=chat_ids)


class MatchFeedSender:
    """N'importe quel échec réseau est journalisé et renvoie ``None``/``False`` — jamais levé : un
    problème d'envoi Telegram ne doit jamais interrompre la collecte des cotes (même principe que
    ``AlertSender``). Le salon (``chat_id``) est passé à chaque appel, pas fixé à la construction :
    un seul envoyeur suffit pour toutes les ligues, chacune avec son propre salon."""

    def __init__(self, bot_token: str | None, *, timeout: float = 10.0):
        self._bot_token = bot_token
        self._timeout = timeout

    async def send(self, chat_id: str, text: str) -> int | None:
        """Envoie un nouveau message. Retourne son identifiant, ou ``None`` en cas d'échec."""
        url = f"{API_BASE}/bot{self._bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={"chat_id": chat_id, "text": text})
                response.raise_for_status()
                return response.json()["result"]["message_id"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("échec d'envoi du fil de match (salon %s) : %s: %s", chat_id, type(exc).__name__, exc)
            return None

    async def edit(self, chat_id: str, message_id: int, text: str) -> bool:
        """Édite un message existant. ``False`` en cas d'échec (ex. message trop ancien pour être
        édité, ou supprimé manuellement) — l'appelant peut alors renvoyer un nouveau message."""
        url = f"{API_BASE}/bot{self._bot_token}/editMessageText"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={
                    "chat_id": chat_id, "message_id": message_id, "text": text,
                })
                response.raise_for_status()
                return True
        except httpx.HTTPError as exc:
            log.warning("échec d'édition du fil de match (salon %s, message %s) : %s: %s",
                        chat_id, message_id, type(exc).__name__, exc)
            return False

    async def send_or_edit(self, chat_id: str, message_id: int | None, text: str) -> int | None:
        """Édite le message existant s'il y en a un ; sinon (ou si l'édition échoue), en envoie un
        nouveau. Retourne l'identifiant du message à retenir pour la prochaine mise à jour."""
        if message_id is not None:
            if await self.edit(chat_id, message_id, text):
                return message_id
        return await self.send(chat_id, text)

    async def send_photo(self, chat_id: str, photo_path: Path, caption: str) -> int | None:
        """Envoie une photo unique avec légende. Retourne son identifiant, ou ``None`` en cas
        d'échec (fichier disparu, erreur réseau...)."""
        url = f"{API_BASE}/bot{self._bot_token}/sendPhoto"
        try:
            with photo_path.open("rb") as fh:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url, data={"chat_id": chat_id, "caption": caption},
                        files={"photo": (photo_path.name, fh, "image/png")},
                    )
            response.raise_for_status()
            return response.json()["result"]["message_id"]
        except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
            log.error("échec d'envoi de la photo (salon %s) : %s: %s", chat_id, type(exc).__name__, exc)
            return None

    async def send_media_group(self, chat_id: str, photo_paths: list[Path], caption: str) -> int | None:
        """Envoie un album de plusieurs photos (les deux portraits), légende sur la première.
        Retourne l'identifiant du **premier** message de l'album — le seul dont la légende peut
        ensuite être éditée (``edit_caption``), les messages suivants de l'album n'en ont pas."""
        url = f"{API_BASE}/bot{self._bot_token}/sendMediaGroup"
        media = [
            {"type": "photo", "media": f"attach://photo{i}", **({"caption": caption} if i == 0 else {})}
            for i in range(len(photo_paths))
        ]
        opened = []
        try:
            files = {}
            for i, path in enumerate(photo_paths):
                fh = path.open("rb")
                opened.append(fh)
                files[f"photo{i}"] = (path.name, fh, "image/png")
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={"chat_id": chat_id, "media": json.dumps(media)}, files=files)
            response.raise_for_status()
            return response.json()["result"][0]["message_id"]
        except (httpx.HTTPError, KeyError, ValueError, OSError, IndexError) as exc:
            log.error("échec d'envoi de l'album de photos (salon %s) : %s: %s", chat_id, type(exc).__name__, exc)
            return None
        finally:
            for fh in opened:
                fh.close()

    async def edit_caption(self, chat_id: str, message_id: int, caption: str) -> bool:
        """Édite la légende d'un message photo existant (jamais son texte : ``edit`` fait cela
        pour un message texte simple — les deux ne sont pas interchangeables côté Telegram)."""
        url = f"{API_BASE}/bot{self._bot_token}/editMessageCaption"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, data={
                    "chat_id": chat_id, "message_id": message_id, "caption": caption,
                })
                response.raise_for_status()
                return True
        except httpx.HTTPError as exc:
            log.warning("échec d'édition de légende (salon %s, message %s) : %s: %s",
                        chat_id, message_id, type(exc).__name__, exc)
            return False


def format_match_message(opp1_name: str, opp2_name: str, rounds, *, league_name: str,
                          match_no_of_day: int | None = None, match_date=None,
                          finish_threshold: int = 5) -> str:
    """Construit le texte complet à partir de toutes les manches connues (reconstruit à chaque
    fois plutôt qu'ajouté incrémentalement : idempotent, se corrige tout seul si un cycle a été
    manqué, jamais de dérive possible entre le message affiché et l'état réel du match)."""
    lines = [f"🎮 {league_name.upper()}"]
    date_str = match_date.strftime("%d-%m-%Y") if match_date is not None else None
    if match_no_of_day is not None and date_str is not None:
        lines.append(f"📅 Match n°{match_no_of_day} — Journée du {date_str}")
    elif match_no_of_day is not None:
        lines.append(f"📅 Match n°{match_no_of_day}")
    elif date_str is not None:
        lines.append(f"📅 Journée du {date_str}")
    lines.append(f"🥊 {opp1_name} VS {opp2_name}")
    lines.append("")

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
        temps = f"{r.seconds}s" if r.seconds is not None else "?s"
        lines.append(f"💥 Manche {r.round_no} : Vainqueur {r.winner_name} — ⏱️ {temps} — "
                     f"{_finish_emoji(r.finish_di)} {finish} [Score : {tally1}-{tally2}]")

    if tally1 >= finish_threshold or tally2 >= finish_threshold:
        vainqueur = opp1_name if tally1 > tally2 else opp2_name
        lines.append(f"🏆 VAINQUEUR DU MATCH : {vainqueur} ({tally1}-{tally2})")
    return "\n".join(lines)


def format_pre_match_caption(opp1_name: str, opp2_name: str, seconds_remaining: int | None) -> str:
    """Légende de l'annonce pré-match. ``seconds_remaining is None`` signale que le match a
    démarré (dernière édition, voir pre_match.py) — jamais un temps négatif affiché."""
    header = f"🥊 {opp1_name} VS {opp2_name}"
    if seconds_remaining is None:
        return f"{header}\n🔴 Le match commence !"
    seconds_remaining = max(seconds_remaining, 0)
    minutes, secs = divmod(seconds_remaining, 60)
    temps = f"{minutes}:{secs:02d}" if minutes else f"{secs}s"
    return f"{header}\n⏳ Commence dans {temps}"
