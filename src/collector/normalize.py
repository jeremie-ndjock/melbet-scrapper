"""Normalisation d'un match v3 en lignes de cotes.

Identité d'une sélection : ``(groupe, type, paramètre brut)`` — voir Memoire.md, section 6.
``round_no`` et ``line`` sont des champs décodés *supplémentaires*, pour la lisibilité et l'analyse ;
ils ne participent pas à l'identité de la sélection, qui reste toujours le paramètre brut de l'API.

Décodage de ``eventParams.params`` (mesuré sur les deux ligues, section 15 de Memoire.md) :
- absent ou vide : aucune information supplémentaire (ex. groupe 1x2) ;
- une valeur : un round (groupes « par round ») ou une ligne (groupes de type Total) ;
- deux valeurs : round puis ligne (ex. durée du round).

La liste de groupes ci-dessous est une aide de lisibilité, pas une liste fermée : un groupe absent
de ces ensembles est accepté sans erreur, avec ``round_no=None`` (valeur par défaut la plus sûre).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .sources.v3.models import Game

# Marchés dont l'unique paramètre est un numéro de round (ex. « Victoire dans le Round (6) »).
ROUND_ONLY_GROUPS = {1050, 1066, 3037, 3533, 10533}
# Marchés dont l'unique paramètre est une ligne (ex. « Total Plus de (7.5) »).
LINE_ONLY_GROUPS = {17, 15, 62, 912, 10526, 10527}


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip("()"))
    except InvalidOperation:
        return None


def decode_extra(group_id: int, params: list[str] | None) -> tuple[int | None, Decimal | None]:
    """Décode ``eventParams.params`` en (round_no, line)."""
    if not params:
        return None, None
    if len(params) == 1:
        value = _to_decimal(params[0])
        if value is None:
            return None, None
        if group_id in ROUND_ONLY_GROUPS:
            return int(value), None
        # Par défaut (y compris un groupe inconnu) : une valeur seule est traitée comme une ligne,
        # cas le plus fréquent observé (Total, Total 1, Total 2, Totaux supplémentaires...).
        return None, value
    if len(params) >= 2:
        round_value = _to_decimal(params[0])
        line_value = _to_decimal(params[1])
        round_no = int(round_value) if round_value is not None else None
        return round_no, line_value
    return None, None  # pragma: no cover (len(params) == 0 déjà couvert par `if not params`)


@dataclass(frozen=True)
class SnapshotRow:
    """Une ligne prête à écrire dans ``odds_snapshots``. ``odds is None`` signifie une sélection
    retirée par le bookmaker (voir migrations/003_odds_snapshots.sql)."""

    game_id: int
    g: int
    t: int
    param: Decimal
    odds: Decimal | None
    blocked: bool
    is_center: bool
    round_no: int | None
    line: Decimal | None

    @property
    def key(self) -> tuple[int, int, Decimal]:
        return (self.g, self.t, self.param)


def flatten_game(game: Game) -> dict[tuple[int, int, Decimal], SnapshotRow]:
    """Aplati toutes les cotes d'un match en un dictionnaire indexé par l'identité de sélection.

    Un groupe sans libellé connu, un type inconnu, ou des paramètres inattendus n'interrompent pas
    la collecte : la sélection est simplement stockée avec ses valeurs brutes.
    """
    rows: dict[tuple[int, int, Decimal], SnapshotRow] = {}
    for group in game.eventGroups:
        for selection_row in group.events:
            for event in selection_row:
                param = _to_decimal(event.parameter) or Decimal(0)
                round_no, line = decode_extra(group.groupId, event.eventParams.params if event.eventParams else None)
                row = SnapshotRow(
                    game_id=game.id,
                    g=group.groupId,
                    t=event.type,
                    param=param,
                    odds=Decimal(str(event.cf)),
                    blocked=event.blocked,
                    is_center=event.isCenter,
                    round_no=round_no,
                    line=line,
                )
                rows[row.key] = row  # une sélection ne peut apparaître qu'une fois par relevé
    return rows
