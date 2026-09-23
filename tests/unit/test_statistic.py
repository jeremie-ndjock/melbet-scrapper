"""Tests du client ``v3/statistic`` : décodage du tableau des rounds (chaîne JSON imbriquée),
tolérance aux entrées illisibles, et l'appel complet (mock HTTP, données réelles capturées le
2026-09-22 sur un match en cours)."""
from __future__ import annotations

import json

import httpx
import pytest

from collector.sources.v3.statistic import (
    RoundTableEntry,
    SetTableEntry,
    decode_period_table,
    decode_round_table,
    fetch_statistic,
    parse_statistic,
)
from collector.transport.errors import ParserError
from collector.transport.http import HttpClient
from collector.transport.ratelimit import RateLimiter

# Capture réelle (Memoire.md, section 21 : étape « fil de match en direct », 2026-09-22).
REAL_RESPONSE = """{
  "fullScore": "1-3",
  "currentPeriod": 5,
  "currentPeriodName": "5\\u00e8me round",
  "timer": {"timeSec": 497, "timeDirection": 0, "timeRun": true},
  "fullScoreDetail": {"scoreOpp1": 1, "scoreOpp2": 3},
  "statistic": {"main": {
      "RoundTable": "[{\\"DI\\":\\"Fatality\\",\\"FW\\":false,\\"R\\":1,\\"T\\":19,\\"W\\":\\"Johnny Cage\\",\\"WT\\":\\"0\\"},{\\"DI\\":\\"Fatality\\",\\"FW\\":true,\\"R\\":2,\\"T\\":22,\\"W\\":\\"Ferra & Torr\\",\\"WT\\":\\"0\\"},{\\"DI\\":\\"Regular\\",\\"FW\\":false,\\"R\\":3,\\"T\\":29,\\"W\\":\\"Johnny Cage\\",\\"WT\\":\\"0\\"},{\\"DI\\":\\"Fatality\\",\\"FW\\":false,\\"R\\":4,\\"T\\":23,\\"W\\":\\"Johnny Cage\\",\\"WT\\":\\"0\\"}]",
      "id_tourney": "100802"
  }},
  "statusLineStr": "8 minutes",
  "inningsStat": []
}"""

NOT_STARTED_RESPONSE = """{
  "fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 0},
  "statistic": {"main": {"RoundTable": "[]", "id_tourney": "100802"}}
}"""

# Capture réelle (2026-09-23) : AI Table Tennis n'a PAS de champ "statistic" du tout — un vrai
# défaut de production trouvé ce jour-là, voir Memoire.md section 31. "periodScores" est la seule
# source du détail par set, au niveau racine de la réponse (pas imbriqué).
REAL_TABLE_TENNIS_RESPONSE = """{
  "fullScore": "0-1",
  "periodScores": [
    {"period": 1, "scoreOpp1": 4, "scoreOpp2": 11, "periodNameFull": "1er set"},
    {"period": 2, "scoreOpp1": 7, "scoreOpp2": 7, "periodNameFull": "2 Set"}
  ],
  "currentPeriod": 2,
  "currentPeriodName": "2 Set",
  "scoreOpp1": 0,
  "scoreOpp2": 1,
  "serve": 1,
  "periodScoresStr": "4-11,7-7",
  "fullScoreDetail": {"scoreOpp1": 0, "scoreOpp2": 1},
  "statusLineStr": "Événement en cours"
}"""


def test_parse_real_capture_decodes_the_round_table():
    response = parse_statistic(REAL_RESPONSE)
    rounds = decode_round_table(response)
    assert rounds == [
        RoundTableEntry(1, 19, "Johnny Cage", "Fatality", "0", False),
        RoundTableEntry(2, 22, "Ferra & Torr", "Fatality", "0", True),
        RoundTableEntry(3, 29, "Johnny Cage", "Regular", "0", False),
        RoundTableEntry(4, 23, "Johnny Cage", "Fatality", "0", False),
    ]


def test_parse_not_started_match_gives_an_empty_round_table():
    response = parse_statistic(NOT_STARTED_RESPONSE)
    assert decode_round_table(response) == []


def test_parse_statistic_rejects_invalid_json():
    with pytest.raises(ParserError, match="JSON invalide"):
        parse_statistic("{ceci n'est pas du json")


def test_decode_round_table_ignores_an_unreadable_round_table_string(caplog):
    response = parse_statistic('{"statistic": {"main": {"RoundTable": "pas du json"}}}')
    with caplog.at_level("WARNING"):
        rounds = decode_round_table(response)
    assert rounds == []
    assert any("illisible" in r.message for r in caplog.records)


def test_decode_round_table_skips_one_bad_entry_without_losing_the_others(caplog):
    raw = '{"statistic": {"main": {"RoundTable": "[{\\"R\\":1,\\"T\\":10,\\"W\\":\\"A\\",\\"DI\\":\\"Regular\\"}, {\\"R\\":\\"pas un nombre\\"}]"}}}'
    response = parse_statistic(raw)
    with caplog.at_level("WARNING"):
        rounds = decode_round_table(response)
    assert len(rounds) == 1 and rounds[0].round_no == 1
    assert any("manche illisible" in r.message for r in caplog.records)


def test_decode_round_table_sorts_by_round_number():
    raw = ('{"statistic": {"main": {"RoundTable": '
           '"[{\\"R\\":2,\\"T\\":1,\\"W\\":\\"A\\",\\"DI\\":\\"Regular\\"},'
           '{\\"R\\":1,\\"T\\":1,\\"W\\":\\"B\\",\\"DI\\":\\"Regular\\"}]"}}}')
    response = parse_statistic(raw)
    rounds = decode_round_table(response)
    assert [r.round_no for r in rounds] == [1, 2]


def test_decode_period_table_from_the_real_ai_table_tennis_capture():
    """Le set 1 est terminé (4:11) ; le set 2 est en cours (7:7, à égalité) et donc ignoré —
    aucun set officiel ne se termine à égalité, contrairement à un set encore en jeu."""
    response = parse_statistic(REAL_TABLE_TENNIS_RESPONSE)
    sets = decode_period_table(response)
    assert sets == [SetTableEntry(set_no=1, points1=4, points2=11, winner=2)]


def test_decode_period_table_is_empty_when_no_set_is_finished_yet():
    response = parse_statistic('{"periodScores": [{"period": 1, "scoreOpp1": 3, "scoreOpp2": 3}]}')
    assert decode_period_table(response) == []


def test_decode_period_table_is_empty_when_periodscores_is_entirely_absent():
    """``statistic.main.RoundTable`` (Mortal Kombat) est totalement absent de la réponse AI Table
    Tennis, et vice versa : chaque champ a une valeur par défaut sûre, jamais d'erreur de schéma."""
    response = parse_statistic(REAL_RESPONSE)  # une capture Mortal Kombat, sans periodScores
    assert decode_period_table(response) == []


def test_decode_period_table_sorts_by_set_number():
    response = parse_statistic(json.dumps({"periodScores": [
        {"period": 2, "scoreOpp1": 11, "scoreOpp2": 5},
        {"period": 1, "scoreOpp1": 4, "scoreOpp2": 11},
    ]}))
    sets = decode_period_table(response)
    assert [s.set_no for s in sets] == [1, 2]


async def test_fetch_statistic_accepts_a_decode_fn_for_another_sport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=REAL_TABLE_TENNIS_RESPONSE)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    client = HttpClient("https://example.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000), client=inner)

    sets, _ = await fetch_statistic(client, 755424452, {"lng": "fr"}, decode_fn=decode_period_table)

    assert sets == [SetTableEntry(set_no=1, points1=4, points2=11, winner=2)]


async def test_fetch_statistic_calls_the_expected_endpoint_and_sorts_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = str(request.url.query, "ascii")
        seen["path"] = request.url.path
        return httpx.Response(200, text=REAL_RESPONSE)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    client = HttpClient("https://example.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000), client=inner)

    rounds, latency_ms = await fetch_statistic(client, 755197701, {"fcountry": 84, "gr": 2147, "lng": "fr", "ref": 8})

    assert seen["path"] == "/cyber-api/mainfeedlive/web/cyber/v3/statistic"
    assert seen["query"] == "fcountry=84&gameId=755197701&gr=2147&lng=fr&ref=8"  # ordre alphabétique
    assert len(rounds) == 4
    assert isinstance(latency_ms, int)
