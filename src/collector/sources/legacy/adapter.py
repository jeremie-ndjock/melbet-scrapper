"""Traduit les réponses de l'API historique vers les mêmes modèles pydantic que la source
principale (``sources.v3.models``), pour que le normaliseur et le pipeline n'aient pas à savoir
laquelle des deux sources a répondu.

Un point important : le champ ``P`` (legacy) et ``parameter`` (v3) utilisent le **même encodage
brut** (mesuré, Memoire.md section 2.4 et section 15 : ``P=800.255`` ↔ round 8, ligne 0.255... en
fait round encodé en centaines, ligne en centièmes). L'identité d'une sélection reste donc la même
quelle que soit la source qui l'a rapportée : un match qui bascule d'une source à l'autre en cours
de route ne casse pas la continuité de son historique de cotes.
"""
from __future__ import annotations

from ..v3.models import (EventGroup, EventParams, FullScoreDetail, Game, GamesByChampResponse,
                          Lenient, Liga, MarketEvent, Opponent, Scores, Timer, Video)
from .models import ChampGame, GameZipValue, LegacyEventGroup

# Même ensemble que dans le normaliseur (voir normalize.py) : un seul paramètre encode un round
# pour ces groupes-là ; ailleurs (y compris un groupe inconnu), il encode une ligne par défaut.
# Le groupe 1074 (durée du round) encode les deux à la fois (``round*100 + ligne/100``).
from ...normalize import ROUND_ONLY_GROUPS

ROUND_AND_LINE_GROUPS = {1074}


def _decode_legacy_param(group_id: int, p: float | None) -> list[str] | None:
    """Reconstruit l'équivalent de ``eventParams.params`` (v3) à partir du paramètre brut legacy,
    pour que le décodage round/ligne du normaliseur (``normalize.decode_extra``) fonctionne à
    l'identique quelle que soit la source."""
    if p is None:
        return None
    if group_id in ROUND_AND_LINE_GROUPS:
        # P = round * 100 + ligne / 100 (mesuré, Memoire.md section 15 : ex. 600.175 = round 6,
        # ligne 17.5). Sans le "* 100" sur le reste, la ligne serait sous-évaluée d'un facteur 100.
        round_no = int(p // 100)
        line = round((p - round_no * 100) * 100, 4)
        return [str(round_no), str(line)]
    if group_id in ROUND_ONLY_GROUPS:
        return [str(int(p))]
    return [str(p)]  # groupe « ligne seule », ou groupe inconnu : traité comme une ligne (cohérent
    # avec le repli par défaut de decode_extra pour un groupe non répertorié).


def _adapt_group(group: LegacyEventGroup) -> EventGroup:
    events = [
        [
            MarketEvent(
                type=e.T, parameter=e.P, cf=e.C, blocked=e.B, isCenter=bool(e.CE),
                eventParams=EventParams(params=params) if (params := _decode_legacy_param(group.G, e.P)) else None,
            )
            for e in row
        ]
        for row in group.E
    ]
    return EventGroup(groupId=group.G, events=events)


def _opponent(name: str, opponent_id: int | None) -> Opponent:
    return Opponent(fullName=name, opps=[Lenient(id=opponent_id)] if opponent_id is not None else [])


def adapt_game(champ_game: ChampGame, detail: GameZipValue | None) -> Game:
    """Combine la fiche de la ligue (participants, horaire) et le détail du match (cotes, score le
    plus frais) en un ``Game`` v3. Si le détail n'est pas disponible (2e requête en échec), le score
    de la fiche de ligue sert de repli, sans cotes pour ce cycle."""
    sc = detail.SC if detail is not None else champ_game.SC
    update_ts = detail.U if detail is not None else champ_game.S
    groups = [_adapt_group(g) for g in detail.GE] if detail is not None else []

    return Game(
        id=champ_game.I,
        startTs=champ_game.S,
        updateTs=update_ts,
        opponent1=_opponent(champ_game.O1, champ_game.O1I),
        opponent2=_opponent(champ_game.O2, champ_game.O2I),
        scores=Scores(
            fullScore=f"{sc.FS.S1}-{sc.FS.S2}",
            currentPeriod=sc.CP,
            currentPeriodName=sc.CPS,
            timer=Timer(timeSec=sc.TS),
            fullScoreDetail=FullScoreDetail(scoreOpp1=sc.FS.S1, scoreOpp2=sc.FS.S2),
        ),
        eventGroups=groups,
        video=Video(id=None),
    )


def build_response(league_id: int, league_name: str, games: list[Game]) -> GamesByChampResponse:
    return GamesByChampResponse(liga=Liga(id=league_id, name=league_name), gamesCount=len(games), games=games)
