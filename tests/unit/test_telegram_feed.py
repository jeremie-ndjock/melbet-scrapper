"""Tests du fil de match Telegram : configuration multi-salons, envoi/édition simulés, format du
message (demandé le 2026-09-22 : ligue, numéro et date du match, emojis par type de finishing)."""
from __future__ import annotations

import json
import os

import httpx
import pytest

from collector.sources.v3.statistic import RoundTableEntry
from collector.sources.v3.statistic import SetTableEntry
from collector.telegram_feed import (
    CHAT_ID_ENV_PREFIX,
    MatchFeedConfig,
    MatchFeedSender,
    format_match_message,
    format_pre_match_caption,
    format_table_tennis_message,
    load_match_feed_config_from_env,
)

BOT_TOKEN = "123:abc"
MKX, MK3 = 1252965, 2282406


@pytest.fixture(autouse=True)
def _clear_real_chat_id_env(monkeypatch):
    """Le processus de test hérite du vrai .env (deux salons AI Table Tennis y sont configurés en
    production) : sans ce nettoyage, ces variables fuiteraient dans les tests ci-dessous et
    fausseraient leurs assertions d'égalité exacte sur ``config.chat_ids``."""
    for key in [k for k in os.environ if k.startswith(CHAT_ID_ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)


def _round(n, winner, seconds=31, finish="Regular", wt="0", fw=False) -> RoundTableEntry:
    return RoundTableEntry(round_no=n, seconds=seconds, winner_name=winner, finish_di=finish, wt=wt, fw=fw)


def test_config_enabled_requires_a_token_and_at_least_one_chat():
    assert MatchFeedConfig(bot_token="t", chat_ids={MKX: "-1"}).enabled
    assert not MatchFeedConfig(bot_token="t").enabled
    assert not MatchFeedConfig(chat_ids={MKX: "-1"}).enabled
    assert not MatchFeedConfig().enabled


def test_load_match_feed_config_from_env_reads_one_chat_id_per_league(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_MATCH_CHAT_ID_1252965", "-100")
    monkeypatch.setenv("TELEGRAM_MATCH_CHAT_ID_2282406", "-200")
    config = load_match_feed_config_from_env()
    assert config.enabled
    assert config.chat_ids == {1252965: "-100", 2282406: "-200"}


def test_load_match_feed_config_from_env_allows_a_single_league(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_MATCH_CHAT_ID_1252965", "-100")
    monkeypatch.delenv("TELEGRAM_MATCH_CHAT_ID_2282406", raising=False)
    config = load_match_feed_config_from_env()
    assert config.enabled
    assert config.chat_ids == {1252965: "-100"}


def test_load_match_feed_config_from_env_ignores_unrelated_variables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_MATCH_CHAT_ID_ABC", "-999")  # suffixe non numérique : ignoré
    monkeypatch.delenv("TELEGRAM_MATCH_CHAT_ID_1252965", raising=False)
    monkeypatch.delenv("TELEGRAM_MATCH_CHAT_ID_2282406", raising=False)
    config = load_match_feed_config_from_env()
    assert config.chat_ids == {}
    assert not config.enabled


def test_format_message_matches_the_requested_example_style():
    rounds = [
        _round(1, "Liu Kang", seconds=22, finish="Fatality"),
        _round(2, "Liu Kang", seconds=27, finish="Fatality"),
        _round(3, "Liu Kang", seconds=32, finish="Regular"),
        _round(4, "Kung Lao", seconds=22, finish="Regular"),
        _round(5, "Liu Kang", seconds=24, finish="Fatality"),
        _round(6, "Liu Kang", seconds=13, finish="Fatality"),
    ]
    import datetime
    text = format_match_message("Liu Kang", "Kung Lao", rounds, league_name="Mortal Kombat 3",
                                 match_no_of_day=27, match_date=datetime.date(2026, 9, 22))
    assert text.splitlines() == [
        "🎮 MORTAL KOMBAT 3",
        "📅 Match n°27 — Journée du 22-09-2026",
        "🥊 Liu Kang VS Kung Lao",
        "",
        "💥 Manche 1 : Vainqueur Liu Kang — ⏱️ 22s — 💀 Fatality [Score : 1-0]",
        "💥 Manche 2 : Vainqueur Liu Kang — ⏱️ 27s — 💀 Fatality [Score : 2-0]",
        "💥 Manche 3 : Vainqueur Liu Kang — ⏱️ 32s — 🥊 Regular [Score : 3-0]",
        "💥 Manche 4 : Vainqueur Kung Lao — ⏱️ 22s — 🥊 Regular [Score : 3-1]",
        "💥 Manche 5 : Vainqueur Liu Kang — ⏱️ 24s — 💀 Fatality [Score : 4-1]",
        "💥 Manche 6 : Vainqueur Liu Kang — ⏱️ 13s — 💀 Fatality [Score : 5-1]",
        "🏆 VAINQUEUR DU MATCH : Liu Kang (5-1)",
    ]


def test_format_message_uses_a_question_mark_for_an_unknown_finish_type():
    rounds = [_round(1, "Goro", finish="TypeInconnu")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "❓ TypeInconnu" in text


def test_format_message_omits_the_date_and_number_lines_when_not_given():
    rounds = [_round(1, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    lines = text.splitlines()
    assert lines[0] == "🎮 MORTAL KOMBAT X"
    assert lines[1] == "🥊 Goro VS Ermac"  # pas de ligne "📅" : ni numéro ni date fournis


def test_format_table_tennis_message_matches_the_approved_style():
    import datetime
    sets = [
        SetTableEntry(set_no=1, points1=4, points2=11, winner=2),
        SetTableEntry(set_no=2, points1=11, points2=7, winner=1),
    ]
    text = format_table_tennis_message("Truls Moregard", "Felix Lebrun", sets,
                                        league_name="AI Table Tennis Prague",
                                        match_no_of_day=3, match_date=datetime.date(2026, 9, 23))
    assert text.splitlines() == [
        "🎮 AI TABLE TENNIS PRAGUE",
        "📅 Match n°3 — Journée du 23-09-2026",
        "🏓 Truls Moregard VS Felix Lebrun",
        "",
        "🏓 Set 1 : 4-11 — Vainqueur Felix Lebrun [Score de sets : 0-1]",
        "🏓 Set 2 : 11-7 — Vainqueur Truls Moregard [Score de sets : 1-1]",
    ]


def test_format_table_tennis_message_announces_the_winner_only_when_finished():
    sets = [SetTableEntry(set_no=1, points1=11, points2=4, winner=1), SetTableEntry(set_no=2, points1=11, points2=7, winner=1)]
    unfinished = format_table_tennis_message("A", "B", sets, league_name="x", match_finished=False)
    finished = format_table_tennis_message("A", "B", sets, league_name="x", match_finished=True)
    assert "🏆" not in unfinished
    assert "🏆 VAINQUEUR DU MATCH : A (2-0)" in finished


def test_format_message_tolerates_an_unknown_winner_name(caplog):
    rounds = [_round(1, "Quelqu'un d'autre")]
    with caplog.at_level("WARNING"):
        text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "[Score : 0-0]" in text
    assert any("ne correspond à aucun des deux adversaires" in r.message for r in caplog.records)


def _fake_async_client(monkeypatch, captured, *, post_result=None, raise_exc=None):
    class FakeResponse:
        def raise_for_status(self):
            if raise_exc:
                raise raise_exc

        def json(self):
            return post_result or {"result": {"message_id": 777}}

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, data):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)


async def test_send_posts_to_sendmessage_with_the_given_chat_id(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)
    message_id = await sender.send("-999", "texte du match")
    assert message_id == 777
    assert "sendMessage" in captured["url"] and BOT_TOKEN in captured["url"]
    assert captured["data"] == {"chat_id": "-999", "text": "texte du match"}


async def test_edit_posts_to_editmessagetext_with_the_chat_and_message_id(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)
    ok = await sender.edit("-999", 555, "texte mis à jour")
    assert ok is True
    assert "editMessageText" in captured["url"]
    assert captured["data"]["chat_id"] == "-999" and captured["data"]["message_id"] == 555


async def test_send_returns_none_on_http_error_without_raising(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured, raise_exc=httpx.HTTPStatusError("erreur", request=None, response=None))
    sender = MatchFeedSender(BOT_TOKEN)
    assert await sender.send("-999", "x") is None


async def test_edit_returns_false_on_http_error_without_raising(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured, raise_exc=httpx.HTTPStatusError("erreur", request=None, response=None))
    sender = MatchFeedSender(BOT_TOKEN)
    assert await sender.edit("-999", 1, "x") is False


async def test_send_or_edit_edits_when_a_message_id_is_known(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)
    result = await sender.send_or_edit("-999", 123, "texte")
    assert result == 123
    assert "editMessageText" in captured["url"]


async def test_send_or_edit_sends_a_new_message_when_no_message_id_is_known(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)
    result = await sender.send_or_edit("-999", None, "texte")
    assert result == 777
    assert "sendMessage" in captured["url"]


async def test_send_or_edit_falls_back_to_sending_when_edit_fails(monkeypatch):
    """Un message trop ancien pour être édité (ou supprimé) ne doit pas faire perdre la mise à
    jour : un nouveau message est envoyé à la place."""
    calls = []

    class FakeSender(MatchFeedSender):
        async def edit(self, chat_id, message_id, text):
            calls.append(("edit", chat_id, message_id))
            return False

        async def send(self, chat_id, text):
            calls.append(("send", chat_id))
            return 999

    sender = FakeSender(BOT_TOKEN)
    result = await sender.send_or_edit("-999", 123, "texte")
    assert result == 999
    assert calls == [("edit", "-999", 123), ("send", "-999")]


# ---------------------------------------------------------------- annonce pré-match (2026-09-22)

def test_format_pre_match_caption_shows_minutes_and_seconds():
    text = format_pre_match_caption("Goro", "Ermac", 125)
    assert text == "🥊 Goro VS Ermac\n⏳ Commence dans 2:05"


def test_format_pre_match_caption_shows_seconds_only_under_a_minute():
    text = format_pre_match_caption("Goro", "Ermac", 8)
    assert text == "🥊 Goro VS Ermac\n⏳ Commence dans 8s"


def test_format_pre_match_caption_never_shows_a_negative_time():
    text = format_pre_match_caption("Goro", "Ermac", -3)
    assert "⏳ Commence dans 0s" in text


def test_format_pre_match_caption_announces_the_start_when_none():
    text = format_pre_match_caption("Goro", "Ermac", None)
    assert text == "🥊 Goro VS Ermac\n🔴 Le match commence !"


def _fake_async_client_multipart(monkeypatch, captured, *, result=None):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return result or {"result": {"message_id": 42}}

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, data=None, files=None):
            captured["url"] = url
            captured["data"] = data
            captured["files"] = {k: (v[0], v[1].read()) for k, v in (files or {}).items()}
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)


async def test_send_photo_uploads_the_file_with_a_caption(tmp_path, monkeypatch):
    photo = tmp_path / "goro.png"
    photo.write_bytes(b"fake-bytes")
    captured = {}
    _fake_async_client_multipart(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)

    message_id = await sender.send_photo("-999", photo, "légende")

    assert message_id == 42
    assert "sendPhoto" in captured["url"]
    assert captured["data"] == {"chat_id": "-999", "caption": "légende"}
    assert captured["files"]["photo"] == ("goro.png", b"fake-bytes")


async def test_send_photo_returns_none_when_the_file_is_missing(tmp_path, monkeypatch):
    sender = MatchFeedSender(BOT_TOKEN)
    assert await sender.send_photo("-999", tmp_path / "absent.png", "x") is None


async def test_send_media_group_uploads_both_photos_with_caption_on_the_first_only(tmp_path, monkeypatch):
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    p1.write_bytes(b"aaa")
    p2.write_bytes(b"bbb")
    captured = {}
    _fake_async_client_multipart(monkeypatch, captured, result={"result": [{"message_id": 10}, {"message_id": 11}]})
    sender = MatchFeedSender(BOT_TOKEN)

    message_id = await sender.send_media_group("-999", [p1, p2], "légende")

    assert message_id == 10  # le premier message de l'album, seul éditable ensuite
    assert "sendMediaGroup" in captured["url"]
    media = json.loads(captured["data"]["media"])
    assert media[0]["caption"] == "légende" and "caption" not in media[1]
    assert captured["files"]["photo0"] == ("a.png", b"aaa")
    assert captured["files"]["photo1"] == ("b.png", b"bbb")


async def test_edit_caption_posts_to_editmessagecaption(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(BOT_TOKEN)

    ok = await sender.edit_caption("-999", 55, "nouvelle légende")

    assert ok is True
    assert "editMessageCaption" in captured["url"]
    assert captured["data"] == {"chat_id": "-999", "message_id": 55, "caption": "nouvelle légende"}


async def test_edit_caption_returns_false_on_http_error_without_raising(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured, raise_exc=httpx.HTTPStatusError("erreur", request=None, response=None))
    sender = MatchFeedSender(BOT_TOKEN)
    assert await sender.edit_caption("-999", 1, "x") is False
