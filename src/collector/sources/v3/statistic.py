"""Appel de l'endpoint ``v3/statistic`` : tableau des rounds en direct (Memoire.md, section 15,
« Tableau des rounds »).

Contrairement aux résultats officiels (``results.py``), disponible seulement après la fin du
match, cette réponse se met à jour manche par manche, dès qu'une manche se termine — c'est la
source qui permet une notification vraiment en temps réel. Vérifié en conditions réelles le
2026-09-22 sur trois matchs (en cours, tout juste commencé, terminé) : jamais de 204 tant que le
match reste listé par ``gamesByChamp`` (le 204 documenté en reconnaissance correspond à un match
déjà disparu de la liste, donc jamais interrogé par ce collecteur).

Champ notable : ``statistic.main.RoundTable`` n'est pas un objet JSON imbriqué mais une **chaîne**
contenant du JSON (à décoder une seconde fois) : ``"[{\"DI\":\"Fatality\",\"FW\":true,\"R\":2,
\"T\":22,\"W\":\"Ferra & Torr\",\"WT\":\"0\"}, ...]"``. Une entrée manque une manche = round pas
encore terminé, jamais une erreur.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from ...transport.errors import ParserError
from ...transport.http import HttpClient
from .models import FullScoreDetail, Lenient, PeriodScore

log = logging.getLogger("collector.statistic")

ENDPOINT = "/cyber-api/mainfeedlive/web/cyber/v3/statistic"


class StatisticMain(Lenient):
    RoundTable: str = "[]"


class StatisticBlock(Lenient):
    main: StatisticMain = StatisticMain()


class StatisticResponse(Lenient):
    fullScoreDetail: FullScoreDetail = FullScoreDetail()
    currentPeriodName: str | None = None
    statistic: StatisticBlock = StatisticBlock()
    # Absent pour Mortal Kombat (tout est dans statistic.main.RoundTable) ; pour AI Table Tennis,
    # c'est au contraire la SEULE source du détail par set — RoundTable n'existe pas du tout pour
    # ce sport (découvert le 2026-09-23 : le fil de match restait silencieux sans jamais journaliser
    # d'erreur, voir decode_period_table).
    periodScores: list[PeriodScore] = []


@dataclass(frozen=True)
class RoundTableEntry:
    round_no: int
    seconds: int | None
    winner_name: str
    finish_di: str | None
    wt: str | None
    fw: bool | None


@dataclass(frozen=True)
class SetTableEntry:
    """Équivalent de ``RoundTableEntry`` pour AI Table Tennis : un set terminé, décodé depuis
    ``periodScores`` (pas de type de finish ni de Mercy pour ce sport). ``winner`` est déjà un
    index (1/2), pas un nom : ``periodScores`` ne donne que des scores, jamais de nom de joueur."""
    set_no: int
    points1: int
    points2: int
    winner: int  # 1 ou 2 ; jamais None ici (voir decode_period_table)


def parse_statistic(raw_text: str) -> StatisticResponse:
    """Valide le texte brut. Lève ``ParserError`` sur JSON invalide ou schéma inattendu — mêmes
    règles que les autres sources v3 (voir sources/v3/client.py)."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ParserError(f"JSON invalide : {exc}", endpoint=ENDPOINT, payload=raw_text) from exc
    try:
        return StatisticResponse.model_validate(data)
    except ValidationError as exc:
        raise ParserError(f"schéma inattendu : {exc}", endpoint=ENDPOINT, payload=raw_text) from exc


def decode_round_table(response: StatisticResponse) -> list[RoundTableEntry]:
    """Décode la chaîne ``RoundTable``, triée par numéro de manche. Une entrée individuelle
    illisible est ignorée avec un avertissement (pas fatale), comme ``results.parse_score`` :
    un défaut de forme sur une manche ne doit jamais faire perdre tout le tableau."""
    raw = response.statistic.main.RoundTable
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("RoundTable illisible, ignorée : %r", raw[:200])
        return []
    entries: list[RoundTableEntry] = []
    for item in items:
        try:
            entries.append(RoundTableEntry(
                round_no=int(item["R"]),
                seconds=int(item["T"]) if item.get("T") is not None else None,
                winner_name=str(item["W"]),
                finish_di=item.get("DI"),
                wt=item.get("WT"),
                fw=item.get("FW"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("manche illisible dans RoundTable, ignorée (%s) : %r", exc, item)
    entries.sort(key=lambda e: e.round_no)
    return entries


def decode_period_table(response: StatisticResponse) -> list[SetTableEntry]:
    """Décode ``periodScores`` en sets **terminés** uniquement, triés par numéro de set. Contrairement
    à ``RoundTable`` (qui ne contient déjà que des manches terminées), ``periodScores`` inclut aussi
    le set en cours — un set à score égal (ex. 7:7) est donc ignoré comme non terminé (aucun set
    officiel ne se termine à égalité), même tolérance que ``results.parse_table_tennis_score``."""
    entries: list[SetTableEntry] = []
    for p in response.periodScores:
        if p.scoreOpp1 == p.scoreOpp2:
            continue  # set en cours (ou anomalie) : pas encore terminé, ignoré pour l'instant
        winner = 1 if p.scoreOpp1 > p.scoreOpp2 else 2
        entries.append(SetTableEntry(set_no=p.period, points1=p.scoreOpp1, points2=p.scoreOpp2, winner=winner))
    entries.sort(key=lambda e: e.set_no)
    return entries


async def fetch_statistic(client: HttpClient, game_id: int, site_params: dict[str, object], *,
                           decode_fn=decode_round_table) -> tuple[list, int]:
    """Appelle l'endpoint pour un match. Retourne (manches/sets connus, latence en ms).

    ``decode_fn`` rend cette fonction réutilisable pour n'importe quel sport (passer
    ``decode_period_table`` pour AI Table Tennis), même principe que ``parse_fn`` dans
    ``results.py`` : la requête HTTP et la validation du schéma commun ne changent jamais, seul le
    décodage du détail par manche/set diffère d'un sport à l'autre."""
    params = {"gameId": game_id, **site_params}
    result = await client.get(ENDPOINT, params)
    response = parse_statistic(result.body)
    return decode_fn(response), result.latency_ms
