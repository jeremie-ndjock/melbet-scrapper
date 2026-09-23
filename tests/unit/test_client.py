"""Tests de la validation de schéma (parseur v3)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from collector.sources.v3.client import fetch_games_by_champ, parse_games_by_champ
from collector.transport.errors import ParserError
from collector.transport.http import HttpClient
from collector.transport.ratelimit import RateLimiter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _first_fixture_body():
    fixture = json.loads((FIXTURES / "mkx_full_match_754878439.json").read_text(encoding="utf-8"))
    record = fixture["records"][0]
    return {"liga": {"id": fixture["league_id"], "name": "Mortal Kombat X"}, "gamesCount": 1, "games": [record["game"]]}


def test_parses_a_real_captured_payload():
    body = json.dumps(_first_fixture_body())
    response = parse_games_by_champ(body)
    assert response.liga.id == 1252965
    assert response.games[0].id == 754878439
    assert response.games[0].eventGroups  # les cotes ont bien été lues


def test_invalid_json_raises_parser_error():
    with pytest.raises(ParserError, match="JSON invalide"):
        parse_games_by_champ("{ceci n'est pas du JSON")


def test_missing_required_field_raises_parser_error():
    body = _first_fixture_body()
    del body["games"][0]["id"]  # champ requis par le modèle
    with pytest.raises(ParserError, match="schéma inattendu"):
        parse_games_by_champ(json.dumps(body))


def test_unknown_extra_field_is_tolerated():
    """Un champ ajouté par le site (évolution de l'API) ne doit jamais interrompre la collecte."""
    body = _first_fixture_body()
    body["games"][0]["champNouveauChampInconnu"] = {"peu importe": True}
    body["nouvelleCleRacine"] = 42
    response = parse_games_by_champ(json.dumps(body))
    assert response.games[0].id == 754878439


def test_unknown_market_group_and_type_are_tolerated():
    """Un groupe ou un type de marché encore jamais vu doit être accepté tel quel."""
    body = _first_fixture_body()
    body["games"][0]["eventGroups"].append({"groupId": 999999, "events": [[{"type": 888888, "cf": 1.23}]]})
    response = parse_games_by_champ(json.dumps(body))
    unknown = next(g for g in response.games[0].eventGroups if g.groupId == 999999)
    assert unknown.events[0][0].type == 888888


async def test_fetch_games_by_champ_treats_204_as_zero_games_not_a_schema_error():
    """Régression du vrai défaut de production trouvé le 2026-09-23 (Memoire.md section 32) :
    un 204 No Content (aucun match en ce moment pour cette ligue — arrivé avec AI Table Tennis,
    jamais avec Mortal Kombat) déclenchait à tort une bascule sur la source de secours, qui ne
    gère d'ailleurs pas ce sport."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    client = HttpClient("https://example.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000), client=inner)

    response, latency_ms = await fetch_games_by_champ(client, 3066896, {"lng": "fr"})

    assert response.liga.id == 3066896
    assert response.gamesCount == 0
    assert response.games == []
    assert isinstance(latency_ms, int)


async def test_fetch_games_by_champ_still_validates_a_normal_200_response():
    body = json.dumps(_first_fixture_body())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    client = HttpClient("https://example.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000), client=inner)

    response, _ = await fetch_games_by_champ(client, 1252965, {"lng": "fr"})

    assert response.liga.id == 1252965
    assert response.games[0].id == 754878439
