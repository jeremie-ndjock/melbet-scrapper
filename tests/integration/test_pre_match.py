"""Tests de l'annonce pré-match : compte à rebours (réutilise le compte à rebours du site,
``timer.timeDirection``), portraits si disponibles, limitation à une édition par ~10 s, et
transition propre au démarrage du match. Base réelle (TimescaleDB) ; aucun réseau réel (Telegram
simulé)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from collector.pre_match import PreMatchAnnouncer, MIN_SECONDS_BETWEEN_EDITS
from collector.sources.v3.models import Game, GamesByChampResponse
from collector.storage.writer import upsert_event

GAME_ID = 900001
LEAGUE_ID = 1252965


@pytest.fixture(autouse=True)
def no_images_by_default(monkeypatch):
    """Le dépôt réel contient désormais de vraies images pour « Goro »/« Ermac » (voir
    assets/fighters/) : sans ceci, ces tests dépendraient de cet état ambiant plutôt que de tester
    le comportement « aucune image connue » qu'ils visent. Un test qui veut de vraies images
    remonte ce correctif explicitement (voir test_images_are_used_as_a_media_group)."""
    monkeypatch.setattr("collector.pre_match.image_path", lambda name: None)


def _game(time_sec: int | None, time_direction: int | None, score1: int = 0, score2: int = 0) -> Game:
    return GamesByChampResponse.model_validate({
        "liga": {"id": LEAGUE_ID, "name": "x"}, "gamesCount": 1, "games": [{
            "id": GAME_ID, "startTs": 1000, "updateTs": 1000,
            "opponent1": {"fullName": "Goro", "opps": [{"id": 1}]},
            "opponent2": {"fullName": "Ermac", "opps": [{"id": 2}]},
            "scores": {"fullScore": f"{score1}-{score2}", "currentPeriodName": "1er round",
                       "fullScoreDetail": {"scoreOpp1": score1, "scoreOpp2": score2},
                       "timer": {"timeSec": time_sec, "timeDirection": time_direction}},
            "eventGroups": [],
        }],
    }).games[0]


class RecordingSender:
    def __init__(self):
        self.sent: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self.edited_captions: list[tuple[int, str]] = []
        self.photos_sent: list[tuple[str, str]] = []
        self.media_groups_sent: list[tuple[list, str]] = []
        self._next_id = 2000

    async def send(self, chat_id, text):
        self.sent.append(text)
        self._next_id += 1
        return self._next_id

    async def edit(self, chat_id, message_id, text):
        self.edited.append((message_id, text))
        return True

    async def send_photo(self, chat_id, photo_path, caption):
        self.photos_sent.append((str(photo_path), caption))
        self._next_id += 1
        return self._next_id

    async def send_media_group(self, chat_id, photo_paths, caption):
        self.media_groups_sent.append((photo_paths, caption))
        self._next_id += 1
        return self._next_id

    async def edit_caption(self, chat_id, message_id, caption):
        self.edited_captions.append((message_id, caption))
        return True


async def _seed_event(db, game: Game) -> None:
    await upsert_event(db, game, league_id=LEAGUE_ID, status="scheduled", seen_at=datetime.now(timezone.utc))


async def test_first_countdown_sighting_sends_a_text_announcement_without_images(db):
    """Aucune image connue pour ces combattants dans ce test : retombe sur du texte, sans erreur."""
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=90, time_direction=-1)
    await _seed_event(db, game)

    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    assert sender.sent == ["🥊 Goro VS Ermac\n⏳ Commence dans 1:30"]
    assert sender.media_groups_sent == [] and sender.photos_sent == []


async def test_a_league_without_a_configured_chat_is_skipped(db):
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={})  # aucun salon configuré
    game = _game(time_sec=90, time_direction=-1)
    await _seed_event(db, game)

    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    assert sender.sent == []


async def test_countdown_edit_is_throttled_to_at_most_once_per_ten_seconds(db, monkeypatch):
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=90, time_direction=-1)
    await _seed_event(db, game)
    await ann.process_games(db, [game], league_id=LEAGUE_ID)  # premier envoi

    # Le temps affiché a changé (85 au lieu de 90) mais moins de 10 s réelles ne se sont
    # écoulées : ne doit pas encore éditer.
    game2 = _game(time_sec=85, time_direction=-1)
    await ann.process_games(db, [game2], league_id=LEAGUE_ID)

    assert sender.edited == []

    # Simule le passage du temps minimal requis.
    ann._last_edit[GAME_ID] -= MIN_SECONDS_BETWEEN_EDITS + 1
    await ann.process_games(db, [game2], league_id=LEAGUE_ID)

    assert len(sender.edited) == 1
    assert "0:25" not in sender.edited[0][1]  # sanity : on affiche bien 1:25, pas une valeur incohérente
    assert "1:25" in sender.edited[0][1]


async def test_unchanged_countdown_value_is_not_re_sent(db):
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=90, time_direction=-1)
    await _seed_event(db, game)
    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    ann._last_edit[GAME_ID] -= MIN_SECONDS_BETWEEN_EDITS + 1
    await ann.process_games(db, [game], league_id=LEAGUE_ID)  # même valeur (90) : rien de neuf

    assert sender.edited == []


async def test_match_start_sends_a_final_edit_and_marks_the_announcement_done(db):
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=5, time_direction=-1)
    await _seed_event(db, game)
    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    started = _game(time_sec=3, time_direction=0)  # le match a démarré : compte à rebours terminé
    await ann.process_games(db, [started], league_id=LEAGUE_ID)

    assert sender.edited[-1][1] == "🥊 Goro VS Ermac\n🔴 Le match commence !"
    done = await db.fetchval("SELECT done FROM match_announcements WHERE game_id = $1", GAME_ID)
    assert done is True

    # Une fois marqué terminé, plus aucune édition supplémentaire ne doit avoir lieu.
    edits_before = len(sender.edited)
    await ann.process_games(db, [started], league_id=LEAGUE_ID)
    assert len(sender.edited) == edits_before


async def test_a_match_started_before_ever_being_announced_is_left_alone(db):
    """Redémarrage du collecteur en plein match, ou match jamais vu en compte à rebours : pas
    d'annonce a posteriori (le fil manche par manche prendra le relais normalement)."""
    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=120, time_direction=0, score1=1, score2=0)  # déjà en cours
    await _seed_event(db, game)

    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    assert sender.sent == [] and sender.edited == []


async def test_images_are_used_as_a_media_group_when_both_fighters_have_one(db, tmp_path, monkeypatch):
    from collector import fighter_images
    import json
    (tmp_path / "goro.png").write_bytes(b"g")
    (tmp_path / "ermac.png").write_bytes(b"e")
    (tmp_path / "_mapping.json").write_text(
        json.dumps({"Goro": "goro.png", "Ermac": "ermac.png"}), encoding="utf-8")
    monkeypatch.setattr(fighter_images, "_MAPPING", fighter_images._load_mapping(tmp_path))
    monkeypatch.setattr(fighter_images, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr("collector.pre_match.image_path", fighter_images.image_path)

    sender = RecordingSender()
    ann = PreMatchAnnouncer(sender=sender, chat_ids={LEAGUE_ID: "-1"})
    game = _game(time_sec=90, time_direction=-1)
    await _seed_event(db, game)

    await ann.process_games(db, [game], league_id=LEAGUE_ID)

    assert len(sender.media_groups_sent) == 1
    photos, caption = sender.media_groups_sent[0]
    assert len(photos) == 2 and "⏳ Commence dans 1:30" in caption
