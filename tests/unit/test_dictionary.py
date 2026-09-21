"""Tests du dictionnaire des marchés, à partir des fichiers réellement téléchargés du CDN
(tests/fixtures/dictionary/). Aucun réseau réel (httpx.MockTransport)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from collector.dictionary import MarketDictionary
from collector.transport.errors import ParserError
from collector.transport.http import HttpClient
from collector.transport.ratelimit import RateLimiter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dictionary"


def make_client(requested: list[str] | None = None) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(request.url.path)
        name = "map.json" if request.url.path.endswith("map_full_fr.json") else request.url.path.rsplit("_", 1)[-1]
        path = FIXTURES / ("map.json" if name == "map.json" else f"chunk_{name}")
        if not path.exists():
            return httpx.Response(404)
        return httpx.Response(200, text=path.read_text(encoding="utf-8"))

    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(transport=transport, base_url="https://cdn.test")
    return HttpClient("https://cdn.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000), client=inner)


# Libellés officiels confirmés lors de la reconnaissance (Memoire.md, section 15).
KNOWN_LABELS = {
    (1, 1): ("1x2", "V1"),
    (1, 3): ("1x2", "V2"),
    (1050, 2140): ("Victoire dans le Round", "V1 dans le Round ()"),
    (1050, 2141): ("Victoire dans le Round", "V2 dans le Round ()"),
    (1074, 2170): ("Durée du Round", "Durée du Round () Plus de ()"),
    (17, 9): ("Total", "Total Plus de ()"),
    (15, 11): ("Total 1", "Total Individuel 1 Plus de ()"),
    (62, 13): ("Total 2", "Total Individuel 2 Plus de ()"),
    (3037, 4055): ("Flawless Victory dans Round", "Flawless Victory dans () Round - Oui"),
    (3533, 4929): ("Fatality dans Round", "Fatality dans Round () - Oui"),
    (1066, 4057): ("Mode de Victoire Dans Le Round", "Fatality dans () Round"),
    (1066, 13550): ("Mode de Victoire Dans Le Round", "Amitié dans Round ()"),
    (912, 4060): ("Totaux supplémentaires", "Total Fatalities Plus de ()"),
    (10526, 14009): ("Total de Babality", "Total Babality Plus de ()"),
    # Incohérence réelle du site : le libellé du groupe a une apostrophe, celui de la sélection non.
    (10527, 14011): ("Total d'Animality", "Total Animality Plus de ()"),
    (10533, 14023): ("Mercy dans le Round", "Mercy dans le Round () - Les deux joueurs"),
}


@pytest.mark.parametrize("key, expected", list(KNOWN_LABELS.items()))
async def test_known_labels_match_the_official_dictionary(key, expected):
    client = make_client()
    d = MarketDictionary()
    assert await d.label_for(client, *key) == expected


async def test_unknown_group_returns_none_without_error():
    client = make_client()
    d = MarketDictionary()
    assert await d.label_for(client, 999999, 1) is None


async def test_unknown_type_within_a_known_group_returns_none():
    client = make_client()
    d = MarketDictionary()
    assert await d.label_for(client, 1, 999999) is None


async def test_chunk_is_fetched_once_and_cached():
    requested = []
    client = make_client(requested)
    d = MarketDictionary()
    await d.label_for(client, 1, 1)
    await d.label_for(client, 1, 3)          # même groupe, même chunk
    await d.label_for(client, 1050, 2140)    # groupe différent, chunk différent
    await d.label_for(client, 1050, 2141)    # même chunk que le précédent
    chunk_requests = [p for p in requested if "map_full_fr" not in p]
    assert len(chunk_requests) == 2          # un seul appel réseau par chunk, malgré 4 résolutions
    assert requested.count(requested[0]) == 1  # la carte elle-même n'est chargée qu'une fois


async def test_resolve_many_skips_unknown_pairs():
    client = make_client()
    d = MarketDictionary()
    resolved = await d.resolve_many(client, {(1, 1), (999999, 1)})
    assert set(resolved) == {(1, 1)}


async def test_invalid_map_raises_parser_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{ceci n'est pas du JSON")
    client = HttpClient("https://cdn.test", "OddsCollector/1.0", rate_limiter=RateLimiter(1000),
                         client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://cdn.test"))
    with pytest.raises(ParserError):
        await MarketDictionary().label_for(client, 1, 1)
