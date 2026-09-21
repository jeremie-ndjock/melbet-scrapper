"""Vérification manuelle, bornée : la source de secours (legacy) répond-elle encore comme prévu ?

Ce script n'est pas un test automatisé (il touche le vrai site) : c'est un outil de diagnostic
ponctuel, à lancer manuellement si l'on veut s'assurer que la source de secours fonctionnerait
avant de compter dessus. Identification honnête, une poignée de requêtes seulement.

Usage : python scripts/check_legacy_live.py
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from collector.sources.legacy.client import fetch_games_by_champ_legacy  # noqa: E402
from collector.transport.http import HttpClient  # noqa: E402
from collector.transport.ratelimit import RateLimiter  # noqa: E402

LEAGUE_ID = 1252965  # Mortal Kombat X
LEAGUE_NAME = "Mortal Kombat X"
SITE_PARAMS = {"country": 84, "partner": 8, "lng": "fr"}


async def main() -> None:
    http = HttpClient("https://melbet-cm.com", "OddsCollector/1.0", rate_limiter=RateLimiter(1.0))
    try:
        response, latency_ms = await fetch_games_by_champ_legacy(http, LEAGUE_ID, LEAGUE_NAME, SITE_PARAMS)
    finally:
        await http.aclose()

    print(f"OK : {len(response.games)} match(s) via la source de secours, {latency_ms} ms cumulés")
    for game in response.games:
        n_odds = sum(len(row) for grp in game.eventGroups for row in grp.events)
        print(f"  match {game.id} : {game.opponent1.display_name} vs {game.opponent2.display_name}, "
              f"{len(game.eventGroups)} groupe(s), {n_odds} cote(s)")


if __name__ == "__main__":
    asyncio.run(main())
