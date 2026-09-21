"""Appel de l'endpoint ``v3/gamesByChamp`` et validation de la réponse."""
from __future__ import annotations

import json

from pydantic import ValidationError

from ...transport.errors import ParserError
from ...transport.http import HttpClient
from .models import GamesByChampResponse

ENDPOINT = "/cyber-api/mainfeedlive/web/cyber/v3/gamesByChamp"


def parse_games_by_champ(raw_text: str) -> GamesByChampResponse:
    """Valide le texte brut d'une réponse. Lève ``ParserError`` sur JSON invalide ou schéma inattendu."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ParserError(f"JSON invalide : {exc}", endpoint=ENDPOINT, payload=raw_text) from exc
    try:
        return GamesByChampResponse.model_validate(data)
    except ValidationError as exc:
        raise ParserError(f"schéma inattendu : {exc}", endpoint=ENDPOINT, payload=raw_text) from exc


async def fetch_games_by_champ(client: HttpClient, champ_id: int, site_params: dict[str, object]) -> tuple[GamesByChampResponse, int]:
    """Appelle l'endpoint pour une ligue. Retourne (réponse validée, latence en ms)."""
    params = {"cfView": 3, "champId": champ_id, **site_params}
    result = await client.get(ENDPOINT, params)
    return parse_games_by_champ(result.body), result.latency_ms
