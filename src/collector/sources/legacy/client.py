"""Appels de l'API historique (« legacy »)."""
from __future__ import annotations

import json
import time

from pydantic import ValidationError

from ...sources.v3.models import GamesByChampResponse
from ...transport.errors import ParserError
from ...transport.http import HttpClient
from .adapter import adapt_game, build_response
from .models import ChampGame, ChampZipEnvelope, GameZipEnvelope, GameZipValue

CHAMP_ZIP_ENDPOINT = "/service-api/LiveFeed/GetChampZip"
GAME_ZIP_ENDPOINT = "/service-api/LiveFeed/GetGameZip"


async def fetch_champ_zip(client: HttpClient, champ_id: int, site_params: dict[str, object]) -> list[ChampGame]:
    """Liste les matchs de la ligue (sans cotes)."""
    params = {"champ": champ_id, "gr": 0, "virtualSports": "true", **site_params}
    result = await client.get(CHAMP_ZIP_ENDPOINT, params)
    try:
        envelope = ChampZipEnvelope.model_validate(json.loads(result.body))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ParserError(f"GetChampZip invalide : {exc}", endpoint=CHAMP_ZIP_ENDPOINT, payload=result.body) from exc
    if not envelope.Success or envelope.Value is None:
        return []
    return envelope.Value.G


async def fetch_game_zip(client: HttpClient, game_id: int, site_params: dict[str, object]) -> GameZipValue | None:
    """Détail d'un match, avec ses cotes. ``None`` si le match est terminé et a disparu du flux
    (``Value: null``, comportement normal — voir Memoire.md, section 2.4)."""
    params = {"id": game_id, "isSubGames": "true", "GroupEvents": "true", "countevents": 250, "grMode": 4, **site_params}
    result = await client.get(GAME_ZIP_ENDPOINT, params)
    try:
        envelope = GameZipEnvelope.model_validate(json.loads(result.body))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ParserError(f"GetGameZip invalide : {exc}", endpoint=GAME_ZIP_ENDPOINT, payload=result.body) from exc
    if not envelope.Success:
        return None
    return envelope.Value


async def fetch_games_by_champ_legacy(
    client: HttpClient, champ_id: int, league_name: str, site_params: dict[str, object],
) -> tuple[GamesByChampResponse, int]:
    """Équivalent legacy de ``sources.v3.client.fetch_games_by_champ`` : une requête pour lister
    les matchs, puis une par match pour ses cotes (voir le module, ``adapter.py``). Plus coûteux en
    requêtes que le v3 (N+1 au lieu de 1) : n'est utilisé qu'en secours, pas en continu.
    Retourne (réponse au format v3, latence cumulée en ms)."""
    champ_games = await fetch_champ_zip(client, champ_id, site_params)
    total_latency_ms = 0
    games = []
    for champ_game in champ_games:
        t0 = time.perf_counter()
        detail = await fetch_game_zip(client, champ_game.I, site_params)
        total_latency_ms += round((time.perf_counter() - t0) * 1000)
        games.append(adapt_game(champ_game, detail))
    return build_response(champ_id, league_name, games), total_latency_ms
