"""Appel de l'endpoint ``v3/gamesByChamp`` et validation de la réponse."""
from __future__ import annotations

import json

from pydantic import ValidationError

from ...transport.errors import ParserError
from ...transport.http import HttpClient
from .models import GamesByChampResponse, Liga

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
    """Appelle l'endpoint pour une ligue. Retourne (réponse validée, latence en ms).

    Un ``204 No Content`` (corps vide) signifie qu'aucun match n'est actuellement en cours ni
    programmé pour cette ligue — un état normal, jamais rencontré avec Mortal Kombat (toujours un
    match en file) mais fréquent avec AI Table Tennis, dont les « salles » ont parfois un court
    instant sans match entre deux rencontres (trouvé en production le 2026-09-23, Memoire.md
    section 32 : chaque occurrence déclenchait à tort une bascule sur la source de secours, qui ne
    gère d'ailleurs pas ce sport). Traité comme zéro match, pas comme une erreur de schéma."""
    params = {"cfView": 3, "champId": champ_id, **site_params}
    result = await client.get(ENDPOINT, params)
    if result.status_code == 204:
        return GamesByChampResponse(liga=Liga(id=champ_id, name=""), gamesCount=0, games=[]), result.latency_ms
    return parse_games_by_champ(result.body), result.latency_ms
