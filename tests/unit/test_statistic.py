"""Tests du client ``v3/statistic`` : décodage du tableau des rounds (chaîne JSON imbriquée),
tolérance aux entrées illisibles, et l'appel complet (mock HTTP, données réelles capturées le
2026-09-22 sur un match en cours)."""
from __future__ import annotations

import httpx
import pytest

from collector.sources.v3.statistic import (
    RoundTableEntry,
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
