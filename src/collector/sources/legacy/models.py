"""Modèles pydantic de l'API historique (« legacy »), source de secours (Memoire.md, section 2.4).

Deux appels sont nécessaires là où le v3 n'en demande qu'un : ``GetChampZip`` liste les matchs de
la ligue (sans cotes), ``GetGameZip`` donne le détail d'un match (avec ses cotes). L'enveloppe
``{Success, Value}`` est commune aux deux ; un match terminé renvoie ``Value: null``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class FullScore(Lenient):
    S1: int = 0
    S2: int = 0


class ScoreBlock(Lenient):
    FS: FullScore = FullScore()
    CP: int = 0
    CPS: str | None = None
    TS: int | None = None


class ChampGame(Lenient):
    I: int
    O1: str
    O2: str
    O1I: int | None = None
    O2I: int | None = None
    S: int
    SC: ScoreBlock = ScoreBlock()


class ChampZipValue(Lenient):
    G: list[ChampGame] = []


class ChampZipEnvelope(Lenient):
    Success: bool
    Value: ChampZipValue | None = None


class LegacyMarketEvent(Lenient):
    T: int
    P: float | None = None
    C: float
    B: bool = False
    CE: int | None = None


class LegacyEventGroup(Lenient):
    G: int
    E: list[list[LegacyMarketEvent]] = []


class GameZipValue(Lenient):
    I: int
    U: int  # horodatage serveur du relevé (équivalent de updateTs en v3)
    S: int
    O1: str
    O2: str
    O1I: int | None = None
    O2I: int | None = None
    SC: ScoreBlock = ScoreBlock()
    GE: list[LegacyEventGroup] = []


class GameZipEnvelope(Lenient):
    Success: bool
    Value: GameZipValue | None = None
