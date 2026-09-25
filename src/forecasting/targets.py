"""Définition des trois cibles et des correspondances entre codes du site et classes."""
from __future__ import annotations

import math

MKX, MK3 = 1252965, 2282406
LEAGUE_NAMES = {MKX: "Mortal Kombat X", MK3: "Mortal Kombat 3"}

# Ordre fixe des classes de finish : l'indice sert d'étiquette aux modèles multiclasses.
FINISH_CLASSES = ("R", "F", "B", "Ba", "Fr", "An", "Hk")
FINISH_INDEX = {c: i for i, c in enumerate(FINISH_CLASSES)}
FINISH_DI_TO_CODE = {
    "Regular": "R", "Fatality": "F", "Brutality": "B", "Babality": "Ba",
    "Friendship": "Fr", "Animality": "An", "Hara-Kiri": "Hk",
}

G_WINNER, G_FINISH, G_DURATION = 1050, 1066, 1074
T_P1, T_P2 = 2140, 2141
T_OVER, T_UNDER = 2170, 2171
# Marché « Mode de Victoire Dans Le Round » : les 7 classes existent sur MK3 (vérifié en base le
# 2026-09-25) ; « No Finish » correspond à une manche Regular.
FINISH_MARKET_T = {4059: "R", 4057: "F", 4058: "B", 14003: "Ba", 13550: "Fr", 14005: "An", 14007: "Hk"}
FINISH_MARKET_T_ORDERED = tuple(t for c in FINISH_CLASSES for t, cc in FINISH_MARKET_T.items() if cc == c)


def finish_label(finish_code, finish_di) -> str | None:
    """Code de finish officiel en priorité (résultats), sinon libellé du direct. ``None`` si
    inconnu : la manche est alors exclue de la cible, jamais devinée."""
    if isinstance(finish_code, str) and finish_code in FINISH_INDEX:
        return finish_code
    if isinstance(finish_di, str):
        return FINISH_DI_TO_CODE.get(finish_di)
    return None


def duration_label(seconds, line) -> int | None:
    """1 si la manche a duré plus que la ligne. Les lignes sont en x,5 et les durées entières :
    jamais d'égalité à trancher."""
    if seconds is None or line is None:
        return None
    if isinstance(seconds, float) and math.isnan(seconds) or isinstance(line, float) and math.isnan(line):
        return None
    return int(seconds > line)
