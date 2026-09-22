"""Tests du fil de match Telegram : configuration, envoi/édition simulés, format du message."""
from __future__ import annotations

import httpx
import pytest

from collector.sources.v3.statistic import RoundTableEntry
from collector.telegram_feed import (
    MatchFeedConfig,
    MatchFeedSender,
    format_match_message,
    load_match_feed_config_from_env,
)

CONFIG = MatchFeedConfig(bot_token="123:abc", chat_id="-999")


def _round(n, winner, seconds=31, finish="Regular", wt="0", fw=False) -> RoundTableEntry:
    return RoundTableEntry(round_no=n, seconds=seconds, winner_name=winner, finish_di=finish, wt=wt, fw=fw)


def test_config_enabled_requires_both_token_and_chat_id():
    assert MatchFeedConfig(bot_token="t", chat_id="c").enabled
    assert not MatchFeedConfig(bot_token="t").enabled
    assert not MatchFeedConfig(chat_id="c").enabled
    assert not MatchFeedConfig().enabled


def test_load_match_feed_config_from_env_reads_expected_variables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_MATCH_CHAT_ID", "-42")
    config = load_match_feed_config_from_env()
    assert config.enabled and config.chat_id == "-42"


def test_format_message_matches_the_requested_example():
    rounds = [_round(1, "Goro", seconds=31, finish="Regular")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "Goro VS Ermac" in text
    assert "Manche 1 : vainqueur Goro, temps: 31 secondes, Type de finishing: Regular" in text


def test_format_message_includes_the_league_name():
    rounds = [_round(1, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat 3")
    assert "Mortal Kombat 3" in text.splitlines()[0]


def test_format_message_includes_the_match_number_of_the_day_when_given():
    rounds = [_round(1, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X", match_no_of_day=37)
    assert "Match n°37 de la journée" in text.splitlines()[0]


def test_format_message_omits_the_match_number_when_not_given():
    rounds = [_round(1, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "Match n°" not in text


def test_format_message_includes_running_score_per_round():
    rounds = [_round(1, "Goro"), _round(2, "Ermac"), _round(3, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    lines = text.splitlines()
    assert "score 1-0" in lines[3]
    assert "score 1-1" in lines[4]
    assert "score 2-1" in lines[5]


def test_format_message_announces_the_match_winner_at_five_rounds():
    rounds = [_round(i, "Goro") for i in range(1, 6)]  # 5-0
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "🏆 Vainqueur du match : Goro (5-0)" in text


def test_format_message_does_not_announce_a_winner_before_five_rounds():
    rounds = [_round(1, "Goro"), _round(2, "Goro")]
    text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "Vainqueur du match" not in text


def test_format_message_tolerates_an_unknown_winner_name(caplog):
    """Un nom de vainqueur qui ne correspond à aucun des deux adversaires (ex. libellé du site
    légèrement différent) ne doit jamais faire planter le formatage — seulement être journalisé."""
    rounds = [_round(1, "Quelqu'un d'autre")]
    with caplog.at_level("WARNING"):
        text = format_match_message("Goro", "Ermac", rounds, league_name="Mortal Kombat X")
    assert "score 0-0" in text
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


async def test_send_posts_to_sendmessage_and_returns_the_new_message_id(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(CONFIG)
    message_id = await sender.send("texte du match")
    assert message_id == 777
    assert "sendMessage" in captured["url"] and "123:abc" in captured["url"]
    assert captured["data"] == {"chat_id": "-999", "text": "texte du match"}


async def test_edit_posts_to_editmessagetext_with_the_message_id(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(CONFIG)
    ok = await sender.edit(555, "texte mis à jour")
    assert ok is True
    assert "editMessageText" in captured["url"]
    assert captured["data"]["message_id"] == 555


async def test_send_returns_none_on_http_error_without_raising(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured, raise_exc=httpx.HTTPStatusError("erreur", request=None, response=None))
    sender = MatchFeedSender(CONFIG)
    assert await sender.send("x") is None


async def test_edit_returns_false_on_http_error_without_raising(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured, raise_exc=httpx.HTTPStatusError("erreur", request=None, response=None))
    sender = MatchFeedSender(CONFIG)
    assert await sender.edit(1, "x") is False


async def test_send_or_edit_edits_when_a_message_id_is_known(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(CONFIG)
    result = await sender.send_or_edit(123, "texte")
    assert result == 123
    assert "editMessageText" in captured["url"]


async def test_send_or_edit_sends_a_new_message_when_no_message_id_is_known(monkeypatch):
    captured = {}
    _fake_async_client(monkeypatch, captured)
    sender = MatchFeedSender(CONFIG)
    result = await sender.send_or_edit(None, "texte")
    assert result == 777
    assert "sendMessage" in captured["url"]


async def test_send_or_edit_falls_back_to_sending_when_edit_fails(monkeypatch):
    """Un message trop ancien pour être édité (ou supprimé) ne doit pas faire perdre la mise à
    jour : un nouveau message est envoyé à la place."""
    calls = []

    class FakeSender(MatchFeedSender):
        async def edit(self, message_id, text):
            calls.append(("edit", message_id))
            return False

        async def send(self, text):
            calls.append(("send",))
            return 999

    sender = FakeSender(CONFIG)
    result = await sender.send_or_edit(123, "texte")
    assert result == 999
    assert calls == [("edit", 123), ("send",)]
