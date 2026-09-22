"""Fil de match Telegram en direct : détecte une manche nouvellement terminée et publie/édite le
message correspondant (voir telegram_feed.py pour le format et l'envoi).

Fonctionnalité ajoutée le 2026-09-22, hors du plan initial à 11 étapes, à la demande de
l'utilisateur. Optionnelle et désactivée par défaut (pas de ``TELEGRAM_MATCH_CHAT_ID`` dans
``.env``) : son absence ne change rien au fonctionnement du collecteur (voir main.py).

Effet de bord utile indépendant de Telegram : les colonnes ``seconds``/``finish_di``/``wt``/``fw``
de ``round_results`` étaient prévues dès la migration 002 (« tableau des rounds, en direct ») mais
jamais alimentées avant cette fonctionnalité — seuls ``finish_code``/``mercy_*`` arrivaient, et
seulement après la fin du match, via les résultats officiels (``results.py``). Ce module complète
donc aussi ces colonnes en direct, que Telegram soit configuré ou non... mais n'est appelé que si
un ``MatchFeedSender`` est configuré (voir scheduler.py) : sans lui, le tableau des rounds reste
rempli uniquement après coup, comme avant cette fonctionnalité — un choix de simplicité, cette
fonctionnalité restant avant tout pensée pour Telegram.

Appelé depuis ``write_once`` (pas ``poll_once``) : ``round_results`` référence ``events`` par clé
étrangère, et ``events`` n'est upserté que dans ``process_cycle`` (voir pipeline.py) — appeler ceci
avant garantirait une violation de contrainte sur le tout premier relevé d'un match.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg

from .sources.v3.models import Game
from .sources.v3.statistic import RoundTableEntry, fetch_statistic
from .storage import queries
from .telegram_feed import MatchFeedSender, format_match_message
from .transport.errors import BlockedError, ParserError, ServerError
from .transport.http import HttpClient

log = logging.getLogger("collector.live_feed")


def _winner_index(winner_name: str, game: Game) -> int | None:
    if winner_name == game.opponent1.display_name:
        return 1
    if winner_name == game.opponent2.display_name:
        return 2
    return None  # journalisé par telegram_feed.format_match_message ; round_results.winner reste NULL


class LiveFeedProcessor:
    def __init__(self, http: HttpClient, site_params: dict[str, object], sender: MatchFeedSender,
                 chat_ids: dict[int, str]):
        self.http = http
        self.site_params = site_params
        self.sender = sender
        self.chat_ids = chat_ids

    async def process_games(self, conn: asyncpg.Connection, games: list[Game], *,
                             league_id: int, league_name: str) -> None:
        """Traite chaque match du relevé. Une ``BlockedError`` se propage (même politique que
        partout ailleurs : un blocage arrête tout, jamais de contournement) ; toute autre erreur
        est journalisée et n'affecte que ce match, pour ce cycle — le suivant réessaiera."""
        chat_id = self.chat_ids.get(league_id)
        if chat_id is None:
            return  # cette ligue n'a pas (encore) de salon Telegram configuré : rien à faire
        for game in games:
            try:
                await self._process_one(conn, game, league_id=league_id, league_name=league_name, chat_id=chat_id)
            except BlockedError:
                raise
            except (ServerError, ParserError) as exc:
                log.warning("fil de match ignoré ce cycle pour le match %s (%s) : %s",
                            game.id, type(exc).__name__, exc)

    async def _match_no_of_day(self, conn: asyncpg.Connection, league_id: int, game: Game) -> int:
        start = datetime.fromtimestamp(game.startTs, tz=timezone.utc)
        day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        return await conn.fetchval(queries.COUNT_LEAGUE_MATCHES_UP_TO, league_id, day_start, start)

    async def _process_one(self, conn: asyncpg.Connection, game: Game, *, league_id: int,
                            league_name: str, chat_id: str) -> None:
        rounds_played = game.scores.fullScoreDetail.scoreOpp1 + game.scores.fullScoreDetail.scoreOpp2
        if rounds_played == 0:
            return  # match pas encore commencé (ou pas encore de manche terminée) : rien à publier

        existing = await conn.fetchrow(queries.SELECT_MATCH_FEED, game.id)
        last_notified = existing["last_round_notified"] if existing else 0
        already_finished = bool(existing and existing["match_finished"])
        is_now_finished = game.scores.currentPeriodName == "Jeu terminé"

        # Rien de neuf à publier : ni une manche de plus, ni un passage à "terminé" pas encore
        # enregistré. Le libellé « Jeu terminé » peut arriver un cycle après la dernière manche
        # (Memoire.md, section 4) : sans le second terme, ce passage à terminé serait perdu.
        if rounds_played <= last_notified and (already_finished or not is_now_finished):
            return

        rounds, _ = await fetch_statistic(self.http, game.id, self.site_params)
        if len(rounds) < rounds_played:
            return  # le tableau des rounds n'a pas encore rattrapé le score : on réessaiera au prochain cycle
        if len(rounds) <= last_notified and not (is_now_finished and not already_finished):
            return  # rien de neuf non plus une fois le tableau des rounds effectivement consulté

        await self._complete_round_results(conn, game, rounds)

        if existing is None:
            match_no = await self._match_no_of_day(conn, league_id, game)
            await conn.execute(queries.INSERT_MATCH_FEED, game.id, chat_id, match_no)
        else:
            match_no = existing["match_no_of_day"]  # figé au premier calcul, jamais recalculé

        match_date = datetime.fromtimestamp(game.startTs, tz=timezone.utc).date()
        text = format_match_message(game.opponent1.display_name, game.opponent2.display_name, rounds,
                                     league_name=league_name, match_no_of_day=match_no, match_date=match_date)
        message_id = existing["message_id"] if existing else None
        new_message_id = await self.sender.send_or_edit(chat_id, message_id, text)
        if new_message_id is None:
            return  # échec d'envoi (déjà journalisé par MatchFeedSender) : on réessaiera au prochain cycle

        await conn.execute(queries.UPDATE_MATCH_FEED, game.id, new_message_id, len(rounds), is_now_finished)

    @staticmethod
    async def _complete_round_results(conn: asyncpg.Connection, game: Game, rounds: list[RoundTableEntry]) -> None:
        args = [
            (game.id, r.round_no, _winner_index(r.winner_name, game), r.seconds, r.finish_di,
             None, r.wt, r.fw, None, None)
            for r in rounds
        ]
        await conn.executemany(queries.UPSERT_ROUND_RESULT, args)
