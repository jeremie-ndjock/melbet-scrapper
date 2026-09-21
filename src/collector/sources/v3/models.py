"""Modèles pydantic de la réponse ``v3/gamesByChamp``.

Tolérance délibérée : ``extra="allow"`` sur chaque modèle, pour qu'un champ ajouté par le site
n'interrompe pas la collecte (voir Memoire.md, risque « changement de structure »). Seuls les champs
que le collecteur utilise réellement sont déclarés et validés ; un champ manquant parmi eux lève une
``ValidationError`` que l'appelant transforme en ``ParserError``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class EventParams(Lenient):
    params: list[str] | None = None


class MarketEvent(Lenient):
    type: int
    parameter: float | None = None
    cf: float
    blocked: bool = False
    isCenter: bool = False
    eventParams: EventParams | None = None


class EventGroup(Lenient):
    groupId: int
    events: list[list[MarketEvent]] = []


class Timer(Lenient):
    timeSec: int | None = None


class FullScoreDetail(Lenient):
    scoreOpp1: int = 0
    scoreOpp2: int = 0


class Scores(Lenient):
    fullScore: str | None = None
    currentPeriod: int = 0
    currentPeriodName: str | None = None
    timer: Timer = Timer()
    fullScoreDetail: FullScoreDetail = FullScoreDetail()


class Opponent(Lenient):
    nameEng: str | None = None
    fullName: str | None = None
    opps: list[Lenient] = []

    @property
    def display_name(self) -> str:
        return self.fullName or self.nameEng or "?"

    @property
    def opponent_id(self) -> int | None:
        if not self.opps:
            return None
        return getattr(self.opps[0], "id", None)


class Video(Lenient):
    id: str | None = None


class Game(Lenient):
    id: int
    num: int | None = None
    startTs: int
    updateTs: int
    opponent1: Opponent
    opponent2: Opponent
    scores: Scores
    eventGroups: list[EventGroup] = []
    video: Video | None = None


class Liga(Lenient):
    id: int
    name: str


class GamesByChampResponse(Lenient):
    liga: Liga
    gamesCount: int = 0
    games: list[Game] = []
