"""Dictionnaire officiel des libellés de marchés (fichiers statiques du CDN, Memoire.md section 2.3).

Fonctionnement :
1. ``bets_model_map_full_fr.json`` donne, pour chaque « chunk » numéroté, l'intervalle de
   ``groupId`` qu'il couvre.
2. ``bets_model_full_fr_{chunk}.json`` contient les libellés eux-mêmes, chargés à la demande
   (un match n'utilise jamais qu'une poignée de groupes) et mis en cache pour la durée du processus.

Un groupe absent de la table (site jamais vu par nous, ou carte pas encore rafraîchie) ne bloque
rien : ``label_for`` retourne ``None`` et l'appelant continue avec les valeurs brutes.
"""
from __future__ import annotations

import json
import logging

from .storage import queries
from .transport.errors import ParserError
from .transport.http import HttpClient

log = logging.getLogger("collector.dictionary")

MAP_PATH = "/genfiles/cms/betstemplates/bets_model_map_full_fr.json"


def _chunk_path(chunk: int) -> str:
    return f"/genfiles/cms/betstemplates/bets_model_full_fr_{chunk}.json"


class MarketDictionary:
    """Résolution des libellés, avec chargement paresseux et mise en cache des chunks."""

    def __init__(self) -> None:
        self._chunk_ranges: dict[int, tuple[int, int]] | None = None
        self._chunks: dict[int, dict] = {}

    async def _ensure_map_loaded(self, client: HttpClient) -> None:
        if self._chunk_ranges is not None:
            return
        result = await client.get(MAP_PATH)
        try:
            raw = json.loads(result.body)
            self._chunk_ranges = {int(chunk): (int(lo), int(hi)) for chunk, (lo, hi) in raw.items()}
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ParserError(f"table des chunks invalide : {exc}", endpoint=MAP_PATH, payload=result.body) from exc

    def _chunk_for(self, group_id: int) -> int:
        """Le chunk 0 sert de repli pour les groupes « de base » (1x2, Total, Total 1, Total 2, ...)
        qui n'ont pas leur propre intervalle dans la table des chunks (mesuré lors de la
        reconnaissance : la table commence au groupe 4, voir Memoire.md section 2.3)."""
        assert self._chunk_ranges is not None
        for chunk, (lo, hi) in self._chunk_ranges.items():
            if lo <= group_id <= hi:
                return chunk
        return 0

    async def _ensure_chunk_loaded(self, client: HttpClient, chunk: int) -> dict:
        if chunk in self._chunks:
            return self._chunks[chunk]
        path = _chunk_path(chunk)
        result = await client.get(path)
        try:
            data = json.loads(result.body)
            groups = data[str(chunk)]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ParserError(f"chunk de dictionnaire invalide : {exc}", endpoint=path, payload=result.body) from exc
        self._chunks[chunk] = groups
        return groups

    async def label_for(self, client: HttpClient, group_id: int, type_id: int) -> tuple[str, str] | None:
        """Retourne (libellé du groupe, libellé de la sélection), ou ``None`` si inconnu.

        La table des intervalles n'est pas toujours fiable : certains groupes (mesuré pour Total,
        Total 1, Total 2 — groupes 17, 15, 62) vivent en réalité dans le chunk 0 alors que leur
        intervalle numérique désigne un autre chunk. On essaie donc le chunk prévu par la table,
        puis le chunk 0 en repli avant de conclure que le groupe est inconnu.
        """
        await self._ensure_map_loaded(client)
        for chunk in dict.fromkeys((self._chunk_for(group_id), 0)):
            groups = await self._ensure_chunk_loaded(client, chunk)
            group = groups.get(str(group_id))
            if group is None:
                continue
            selection = group.get("M", {}).get(str(type_id))
            if selection is None:
                continue
            return group.get("N", "?"), selection.get("N", "?")
        return None

    async def resolve_many(self, client: HttpClient, keys: set[tuple[int, int]]) -> dict[tuple[int, int], tuple[str, str]]:
        """Résout plusieurs paires (groupe, type) en une fois. Les paires inconnues sont omises."""
        resolved: dict[tuple[int, int], tuple[str, str]] = {}
        for group_id, type_id in keys:
            label = await self.label_for(client, group_id, type_id)
            if label is not None:
                resolved[(group_id, type_id)] = label
        return resolved


async def refresh_unlabeled_markets(client: HttpClient, conn, dictionary: MarketDictionary) -> int:
    """Recherche en base les marchés jamais résolus, les résout, et met à jour ``markets_dict``.

    Conçu pour être appelé périodiquement (voir Memoire.md, section 11 : toutes les 6 h), pas à
    chaque cycle de collecte : les appels réseau vers le CDN restent hors du chemin critique.
    """
    rows = await conn.fetch(queries.SELECT_UNLABELED_MARKETS)
    keys = {(r["g"], r["t"]) for r in rows}
    if not keys:
        return 0
    resolved = await dictionary.resolve_many(client, keys)
    for (group_id, type_id), (group_label, type_label) in resolved.items():
        await conn.execute(queries.UPSERT_MARKET_LABEL, group_id, type_id, group_label, type_label)
    if len(resolved) < len(keys):
        log.warning("%d marché(s) sans libellé connu dans le dictionnaire (sur %d)", len(keys) - len(resolved), len(keys))
    return len(resolved)
