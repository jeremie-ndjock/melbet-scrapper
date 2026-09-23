"""Tests du fil de match en direct : détection d'une nouvelle manche, envoi/édition Telegram,
complétion de ``round_results`` en direct, reprise après redémarrage, et la politique de blocage
(aucune exception). Base réelle (TimescaleDB) ; aucun réseau réel (httpx.MockTransport)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from collector.live_feed import LiveFeedProcessor
from collector.sources.v3.models import Game, GamesByChampResponse
from collector.storage import queries
from collector.storage.writer import upsert_event
from collector.telegram_feed import MatchFeedSender
from collector.transport.errors import BlockedError, ParserError, ServerError
from collector.transport.http import HttpClient, RetryConfig
from collector.transport.ratelimit import RateLimiter

GAME_ID = 755197701
LEAGUE_ID = 1252965
LEAGUE_NAME = "Mortal Kombat X"

AI_TT_GAME_ID = 755424452
AI_TT_LEAGUE_ID = 3066896
AI_TT_LEAGUE_NAME = "AI Table Tennis Prague"


def _game(score1: int, score2: int, period_name: str = "3ème round") -> Game:
    return GamesByChampResponse.model_validate({
        "liga": {"id": 1252965, "name": "x"}, "gamesCount": 1, "games": [{
            "id": GAME_ID, "startTs": 1000, "updateTs": 1000,
            "opponent1": {"fullName": "Goro", "opps": [{"id": 1}]},
            "opponent2": {"fullName": "Ermac", "opps": [{"id": 2}]},
            "scores": {"fullScore": f"{score1}-{score2}", "currentPeriodName": period_name,
                       "fullScoreDetail": {"scoreOpp1": score1, "scoreOpp2": score2}},
            "eventGroups": [],
        }],
    }).games[0]


def _tt_game(score1: int, score2: int, period_name: str = "2 Set") -> Game:
    return GamesByChampResponse.model_validate({
        "liga": {"id": AI_TT_LEAGUE_ID, "name": "x"}, "gamesCount": 1, "games": [{
            "id": AI_TT_GAME_ID, "startTs": 1000, "updateTs": 1000,
            "opponent1": {"fullName": "Truls Moregard", "opps": [{"id": 1}]},
            "opponent2": {"fullName": "Felix Lebrun", "opps": [{"id": 2}]},
            "scores": {"fullScore": f"{score1}-{score2}", "currentPeriodName": period_name,
                       "fullScoreDetail": {"scoreOpp1": score1, "scoreOpp2": score2}},
            "eventGroups": [],
        }],
    }).games[0]


class FakeStatistic:
    """Sert le tableau des rounds courant pour GAME_ID ; ``rounds`` est mutable entre les appels
    du test pour simuler l'avancement du match cycle après cycle."""

    def __init__(self):
        self.rounds: list[dict] = []
        self.calls = 0
        self.blocked = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.blocked:
            return httpx.Response(403, text="bloqué")
        body = {"fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0},
                "statistic": {"main": {"RoundTable": json.dumps(self.rounds)}}}
        return httpx.Response(200, text=json.dumps(body))


class FakeTableTennisStatistic:
    """Équivalent de ``FakeStatistic`` pour AI Table Tennis : ``periodScores`` au lieu de
    ``statistic.main.RoundTable`` (ce champ n'existe pas du tout pour ce sport — un vrai défaut
    de production trouvé le 2026-09-23 : le fil de match restait silencieux sans jamais publier,
    ni journaliser d'erreur, car le décodage cherchait un champ qui n'existe pas pour ce sport)."""

    def __init__(self):
        self.period_scores: list[dict] = []
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = {"fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0}, "periodScores": self.period_scores}
        return httpx.Response(200, text=json.dumps(body))


def make_http(handler) -> HttpClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://melbet.test")
    return HttpClient("https://melbet.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000),
                       client=inner, retry=RetryConfig(max_attempts=1))


class FakeSender(MatchFeedSender):
    def __init__(self):
        super().__init__("t")
        self.sent: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self._next_id = 1000

    async def send(self, chat_id: str, text: str) -> int | None:
        self.sent.append(text)
        self._next_id += 1
        return self._next_id

    async def edit(self, chat_id: str, message_id: int, text: str) -> bool:
        self.edited.append((message_id, text))
        return True


async def _seed_event(db, game: Game, league_id: int = 1252965) -> None:
    await upsert_event(db, game, league_id=league_id, status="live", seen_at=datetime.now(timezone.utc))


async def test_first_completed_round_sends_a_new_message_and_fills_round_results(db):
    stat = FakeStatistic()
    stat.rounds = [{"R": 1, "T": 31, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert len(sender.sent) == 1
    assert "💥 Manche 1 : Vainqueur Goro — ⏱️ 31s — 🥊 Regular [Score : 1-0]" in sender.sent[0]
    row = await db.fetchrow("SELECT winner, seconds, finish_di, wt, fw FROM round_results WHERE game_id = $1 AND round_no = 1", GAME_ID)
    assert row["winner"] == 1 and row["seconds"] == 31 and row["finish_di"] == "Regular" and row["fw"] is False
    feed = await db.fetchrow(queries.SELECT_MATCH_FEED, GAME_ID)
    assert feed["last_round_notified"] == 1 and feed["message_id"] is not None


async def test_second_completed_round_edits_the_existing_message(db):
    stat = FakeStatistic()
    stat.rounds = [{"R": 1, "T": 31, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)
    first_message_id = await db.fetchval("SELECT message_id FROM match_feed WHERE game_id = $1", GAME_ID)

    stat.rounds.append({"R": 2, "T": 45, "W": "Ermac", "DI": "Fatality", "WT": "0", "FW": True})
    game2 = _game(1, 1)
    await proc.process_games(db, [game2], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert len(sender.sent) == 1  # jamais un deuxième message
    assert len(sender.edited) == 1
    edited_id, edited_text = sender.edited[0]
    assert edited_id == first_message_id
    assert "💥 Manche 2 : Vainqueur Ermac" in edited_text
    assert "[Score : 1-1]" in edited_text


async def test_no_new_round_does_not_call_statistic_again(db):
    stat = FakeStatistic()
    stat.rounds = [{"R": 1, "T": 31, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)
    calls_after_first = stat.calls

    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)  # même score : rien de nouveau

    assert stat.calls == calls_after_first
    assert len(sender.sent) == 1 and len(sender.edited) == 0


async def test_match_not_yet_started_is_skipped_without_calling_statistic(db):
    stat = FakeStatistic()
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(0, 0, period_name="1er round")
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert stat.calls == 0
    assert sender.sent == []


async def test_statistic_lagging_behind_the_score_is_retried_next_cycle(db):
    """Le tableau des rounds peut avoir un léger décalage par rapport au score déjà visible dans
    ``gamesByChamp`` (un cycle de retard) : ne pas publier une manche pas encore décrite, réessayer
    au cycle suivant plutôt que d'afficher une information incomplète ou fausse."""
    stat = FakeStatistic()
    stat.rounds = []  # score dit 1-0 mais le tableau des rounds n'a pas encore rattrapé
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)
    assert sender.sent == []  # rien envoyé : pas d'information erronée

    stat.rounds = [{"R": 1, "T": 31, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False}]
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)
    assert len(sender.sent) == 1  # rattrapé au cycle suivant


async def test_match_finished_is_recorded_and_announces_the_winner(db):
    stat = FakeStatistic()
    stat.rounds = [{"R": i, "T": 30, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False} for i in range(1, 6)]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(5, 0, period_name="Jeu terminé")
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert "🏆 VAINQUEUR DU MATCH : Goro (5-0)" in sender.sent[0]
    finished = await db.fetchval("SELECT match_finished FROM match_feed WHERE game_id = $1", GAME_ID)
    assert finished is True


async def test_finished_label_arriving_a_cycle_later_still_updates_match_finished(db):
    """Le libellé « Jeu terminé » peut apparaître un cycle après que le score a atteint son
    total final (Memoire.md, section 4) : le drapeau ``match_finished`` doit quand même finir
    par passer à vrai, sans manche supplémentaire à publier."""
    stat = FakeStatistic()
    stat.rounds = [{"R": i, "T": 30, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False} for i in range(1, 6)]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(5, 0, period_name="5ème round")  # score final déjà atteint, libellé pas encore mis à jour
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)
    assert len(sender.sent) == 1
    finished = await db.fetchval("SELECT match_finished FROM match_feed WHERE game_id = $1", GAME_ID)
    assert finished is False

    game_finished = _game(5, 0, period_name="Jeu terminé")  # même score, libellé mis à jour
    await proc.process_games(db, [game_finished], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert len(sender.sent) == 1  # pas de nouveau message
    assert len(sender.edited) == 1  # le message existant est réédité (même texte, drapeau à jour)
    finished = await db.fetchval("SELECT match_finished FROM match_feed WHERE game_id = $1", GAME_ID)
    assert finished is True


async def test_a_blocked_statistic_endpoint_propagates_blocked_error(db):
    stat = FakeStatistic()
    stat.blocked = True
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    with pytest.raises(BlockedError):
        await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)


async def test_a_transient_statistic_error_is_tolerated_for_this_game_only(db, caplog):
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(broken), site_params={}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    with caplog.at_level("WARNING"):
        await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)  # ne lève rien

    assert sender.sent == []


async def test_ai_table_tennis_completed_set_sends_a_message_and_fills_round_results(db):
    """Régression du vrai défaut trouvé le 2026-09-23 : ``periodScores``, pas ``RoundTable``, et un
    ``winner`` déjà sous forme d'index (pas de nom à comparer, contrairement à Mortal Kombat)."""
    stat = FakeTableTennisStatistic()
    stat.period_scores = [{"period": 1, "scoreOpp1": 4, "scoreOpp2": 11}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender,
                              chat_ids={AI_TT_LEAGUE_ID: "-1"}, league_sport_ids={AI_TT_LEAGUE_ID: 10})

    game = _tt_game(0, 1)
    await _seed_event(db, game, league_id=AI_TT_LEAGUE_ID)
    await proc.process_games(db, [game], league_id=AI_TT_LEAGUE_ID, league_name=AI_TT_LEAGUE_NAME)

    assert len(sender.sent) == 1
    assert "🏓 Set 1 : 4-11 — Vainqueur Felix Lebrun [Score de sets : 0-1]" in sender.sent[0]
    row = await db.fetchrow(
        "SELECT winner, seconds, finish_di, wt, fw FROM round_results WHERE game_id = $1 AND round_no = 1",
        AI_TT_GAME_ID,
    )
    assert row["winner"] == 2  # Felix Lebrun est le joueur 2
    assert row["seconds"] is None and row["finish_di"] is None and row["wt"] is None and row["fw"] is None


async def test_ai_table_tennis_in_progress_set_is_not_mistaken_for_completed(db):
    """Un set en cours (ex. 7:7) apparaît dans ``periodScores`` mais n'est pas encore terminé —
    aucun set officiel ne se termine à égalité. Doit être traité comme le round MK en retard :
    rien publié tant que le tableau n'a pas rattrapé le score visible dans gamesByChamp."""
    stat = FakeTableTennisStatistic()
    stat.period_scores = [{"period": 1, "scoreOpp1": 4, "scoreOpp2": 11}, {"period": 2, "scoreOpp1": 7, "scoreOpp2": 7}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender,
                              chat_ids={AI_TT_LEAGUE_ID: "-1"}, league_sport_ids={AI_TT_LEAGUE_ID: 10})

    game = _tt_game(0, 1)  # un seul set terminé dans gamesByChamp
    await _seed_event(db, game, league_id=AI_TT_LEAGUE_ID)
    await proc.process_games(db, [game], league_id=AI_TT_LEAGUE_ID, league_name=AI_TT_LEAGUE_NAME)

    assert len(sender.sent) == 1  # seul le set 1 (terminé) est publié, jamais le set 2 en cours
    assert "Set 2" not in sender.sent[0]


async def test_ai_table_tennis_finished_match_announces_the_winner(db):
    stat = FakeTableTennisStatistic()
    stat.period_scores = [
        {"period": 1, "scoreOpp1": 11, "scoreOpp2": 4},
        {"period": 2, "scoreOpp1": 11, "scoreOpp2": 7},
    ]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender,
                              chat_ids={AI_TT_LEAGUE_ID: "-1"}, league_sport_ids={AI_TT_LEAGUE_ID: 10})

    game = _tt_game(2, 0, period_name="Jeu terminé")
    await _seed_event(db, game, league_id=AI_TT_LEAGUE_ID)
    await proc.process_games(db, [game], league_id=AI_TT_LEAGUE_ID, league_name=AI_TT_LEAGUE_NAME)

    assert "🏆 VAINQUEUR DU MATCH : Truls Moregard (2-0)" in sender.sent[0]
    finished = await db.fetchval("SELECT match_finished FROM match_feed WHERE game_id = $1", AI_TT_GAME_ID)
    assert finished is True


async def test_a_league_without_sport_mapping_defaults_to_mortal_kombat_decoder(db):
    """Sans entrée dans ``league_sport_ids`` (ex. ``LiveFeedProcessor`` construit sans ce
    paramètre), le décodage Mortal Kombat reste le comportement par défaut — jamais de plantage,
    même s'il ne trouve rien d'utile pour un autre sport (voir Memoire.md, section 31)."""
    stat = FakeStatistic()
    stat.rounds = [{"R": 1, "T": 31, "W": "Goro", "DI": "Regular", "WT": "0", "FW": False}]
    sender = FakeSender()
    proc = LiveFeedProcessor(http=make_http(stat.handler), site_params={"lng": "fr"}, sender=sender, chat_ids={LEAGUE_ID: "-1"})

    game = _game(1, 0)
    await _seed_event(db, game)
    await proc.process_games(db, [game], league_id=LEAGUE_ID, league_name=LEAGUE_NAME)

    assert len(sender.sent) == 1
    assert "💥 Manche 1" in sender.sent[0]
