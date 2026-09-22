"""Annonce pré-match sur Telegram : portraits des deux combattants (si disponibles) et compte à
rebours avant le début, édité toutes les ~10 secondes (demandé le 2026-09-22). Distinct du fil de
match manche par manche (``live_feed.py``), qui ne démarre qu'à la première manche terminée — deux
messages séparés par match, pas le même message qui se transforme.

Réutilise directement ``game.scores.timer.timeSec``/``timeDirection`` : c'est le compte à rebours
du site lui-même (``timeDirection == -1``), pas un calcul refait depuis ``startTs`` — plus fiable
(fait foi côté site) et sans appel réseau supplémentaire (déjà présent dans chaque relevé
``gamesByChamp``, contrairement au tableau des rounds qui nécessite un appel dédié à
``v3/statistic``).

Un combattant sans image connue (voir ``fighter_images.py``) ne bloque rien : l'annonce retombe
sur une seule photo (l'autre combattant) ou sur du texte seul si aucune des deux images n'existe.
"""
from __future__ import annotations

import logging
import time

import asyncpg

from .fighter_images import image_path
from .sources.v3.models import Game
from .storage import queries
from .telegram_feed import MatchFeedSender, format_pre_match_caption

log = logging.getLogger("collector.pre_match")

# Ne pas éditer plus souvent que cela, même si le cycle de sondage est plus rapide (5 s) : demandé
# explicitement ("décrémente toutes les 10 secondes"), et plus respectueux des limites Telegram.
MIN_SECONDS_BETWEEN_EDITS = 10.0


class PreMatchAnnouncer:
    def __init__(self, sender: MatchFeedSender, chat_ids: dict[int, str]):
        self.sender = sender
        self.chat_ids = chat_ids
        # game_id -> horodatage monotone de la dernière édition réussie (en mémoire seulement :
        # une édition manquée après un redémarrage n'est pas grave, la suivante rattrape).
        self._last_edit: dict[int, float] = {}

    async def process_games(self, conn: asyncpg.Connection, games: list[Game], *, league_id: int) -> None:
        chat_id = self.chat_ids.get(league_id)
        if chat_id is None:
            return  # cette ligue n'a pas (encore) de salon Telegram configuré
        for game in games:
            await self._process_one(conn, game, chat_id=chat_id)

    async def _process_one(self, conn: asyncpg.Connection, game: Game, *, chat_id: str) -> None:
        timer = game.scores.timer
        counting_down = timer.timeDirection == -1
        seconds_remaining = timer.timeSec if counting_down else None

        existing = await conn.fetchrow(queries.SELECT_MATCH_ANNOUNCEMENT, game.id)
        if existing and existing["done"]:
            return  # déjà finalisée pour ce match : rien de plus à faire

        if not counting_down:
            if existing and existing["message_id"] is not None:
                await self._edit(chat_id, existing["message_id"], bool(existing["is_photo"]),
                                  game, seconds_remaining=None)
                await conn.execute(queries.UPDATE_MATCH_ANNOUNCEMENT, game.id, existing["message_id"],
                                    existing["is_photo"], None, True)
            # Sinon : match jamais annoncé et déjà démarré (ex. redémarré en cours de compte à
            # rebours) — pas d'annonce a posteriori, le fil manche par manche prendra le relais.
            return

        if existing and existing["last_seconds"] == seconds_remaining:
            return  # le compte à rebours du site n'a pas bougé depuis la dernière fois

        now = time.monotonic()
        last = self._last_edit.get(game.id)
        if existing is not None and last is not None and now - last < MIN_SECONDS_BETWEEN_EDITS:
            return

        caption = format_pre_match_caption(game.opponent1.display_name, game.opponent2.display_name,
                                            seconds_remaining)

        if existing is None:
            await conn.execute(queries.INSERT_MATCH_ANNOUNCEMENT, game.id, chat_id)
            message_id, is_photo = await self._send_first(chat_id, game, caption)
        else:
            message_id, is_photo = existing["message_id"], bool(existing["is_photo"])
            if not await self._edit(chat_id, message_id, is_photo, game, seconds_remaining):
                message_id = await self.sender.send(chat_id, caption)
                is_photo = False

        if message_id is None:
            return  # échec d'envoi (déjà journalisé par MatchFeedSender) : on réessaiera au prochain cycle

        self._last_edit[game.id] = now
        await conn.execute(queries.UPDATE_MATCH_ANNOUNCEMENT, game.id, message_id, is_photo, seconds_remaining, False)

    async def _send_first(self, chat_id: str, game: Game, caption: str) -> tuple[int | None, bool]:
        photo1 = image_path(game.opponent1.display_name)
        photo2 = image_path(game.opponent2.display_name)
        if photo1 and photo2:
            return await self.sender.send_media_group(chat_id, [photo1, photo2], caption), True
        if photo1 or photo2:
            return await self.sender.send_photo(chat_id, photo1 or photo2, caption), True
        return await self.sender.send(chat_id, caption), False

    async def _edit(self, chat_id: str, message_id: int, is_photo: bool, game: Game,
                     seconds_remaining: int | None) -> bool:
        caption = format_pre_match_caption(game.opponent1.display_name, game.opponent2.display_name,
                                            seconds_remaining)
        if is_photo:
            return await self.sender.edit_caption(chat_id, message_id, caption)
        return await self.sender.edit(chat_id, message_id, caption)
