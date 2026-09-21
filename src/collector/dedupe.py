"""Détecteur de changement (option A, voir Memoire.md section 16) : une ligne n'est produite que
si la cote ou le verrou d'une sélection change, ou si la sélection apparaît ou disparaît.

Point important : une sélection retirée n'est signalée **qu'une seule fois**, au moment de son
retrait. Tant qu'elle reste absente, aucune ligne n'est réécrite à chaque cycle — sans quoi une
sélection retirée tôt dans un match produirait une ligne à chaque relevé jusqu'à la fin, annulant
l'essentiel du bénéfice de l'option A. Si elle réapparaît plus tard, elle est traitée comme nouvelle.

L'état de référence est conservé en mémoire, par match. ``preload`` permet de le reconstituer depuis
la base au démarrage (reprise après redémarrage), en lisant la vue ``odds_latest`` (qui donne déjà
la dernière ligne connue de chaque sélection, y compris les sélections retirées avec ``odds=NULL``).
"""
from __future__ import annotations

from decimal import Decimal

from .normalize import SnapshotRow

# État minimal suffisant pour détecter un changement : la cote (None = retirée) et le verrou.
_SelectionState = tuple[Decimal | None, bool]


class ChangeDetector:
    def __init__(self) -> None:
        self._state: dict[int, dict[tuple[int, int, Decimal], _SelectionState]] = {}

    def preload(self, game_id: int, rows: list[SnapshotRow]) -> None:
        """Initialise l'état d'un match à partir de son dernier état connu en base."""
        self._state[game_id] = {row.key: (row.odds, row.blocked) for row in rows}

    def diff(self, game_id: int, current: dict[tuple[int, int, Decimal], SnapshotRow]) -> list[SnapshotRow]:
        """Retourne les lignes à écrire pour ce relevé : nouvelles, changées, ou tout juste retirées."""
        previous = self._state.get(game_id, {})
        changes: list[SnapshotRow] = []
        new_state: dict[tuple[int, int, Decimal], _SelectionState] = dict(previous)

        for key, row in current.items():
            prior = previous.get(key)
            if prior is None or prior != (row.odds, row.blocked):
                changes.append(row)
            new_state[key] = (row.odds, row.blocked)

        for key, prior in previous.items():
            if key in current:
                continue
            if prior[0] is None:
                continue  # déjà signalée comme retirée lors d'un cycle précédent : rien à réécrire
            g, t, param = key
            changes.append(SnapshotRow(game_id, g, t, param, None, False, False, None, None))
            new_state[key] = (None, False)

        self._state[game_id] = new_state
        return changes

    def forget(self, game_id: int) -> None:
        """Libère l'état d'un match terminé et disparu du flux."""
        self._state.pop(game_id, None)
