"""Backtest de la martingale (1000/2000/4000/8000 F, 4 sets au plus) sur les vrais sets AI Table
Tennis (Memoire.md, section 36).

« Manche » = set. Un match se joue en 2 ou 3 sets : une série de 4 sets s'étale donc sur plusieurs
matchs.

Lecture B (principale) : séquence continue des sets, dans l'ordre chronologique, par salle.
Lecture A : le cycle repart à zéro à chaque nouveau match (au plus 3 sets par cycle, les pertes
            d'un cycle interrompu restent acquises).
Choix du joueur : toujours joueur 1 ; suivre le vainqueur du set précédent ; parier contre lui.

Cote utilisée : 1,85 pour les deux joueurs, cote d'ouverture standard d'un marché de set mesurée en
base (la cote ne bouge qu'une fois le set commencé). Comparée à une cote fictive de 2,00, sans
marge, pour isoler l'effet de la marge du bookmaker.

Export des données (sur le VPS, puis rapatrier le fichier) :

    docker compose exec -T db psql -U collector -d odds -c "\\copy (
      SELECT league_id, game_id, date_start, score_raw FROM results
      WHERE league_id IN (3066896, 3066897) ORDER BY league_id, date_start, game_id
    ) TO STDOUT WITH CSV HEADER" > results_tt.csv

Usage : ``python scripts/backtest_martingale_tt.py results_tt.csv``
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict

BASE = 1000
STEPS = 4
VENUES = {3066896: "AI Table Tennis Prague", 3066897: "AI Table Tennis Goa"}
RULES = ("joueur1", "suivre", "contre")
_SET_RE = re.compile(r"(\d+):(\d+)")


def set_winners(score_raw):
    """``"2:1 (11:13,11:6,11:6)"`` -> ``[2, 1, 1]``. Un set à égalité (illisible) est ignoré."""
    inner = score_raw.partition("(")[2]
    return [1 if a > b else 2 for a, b in ((int(x), int(y)) for x, y in _SET_RE.findall(inner)) if a != b]


def load(path):
    """Par salle, la liste des matchs (chacun = vainqueurs de ses sets), dans l'ordre."""
    matches = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sets = set_winners(r["score_raw"])
            if sets:
                matches[int(r["league_id"])].append(sets)
    return matches


def choose(rule, prev_winner):
    if rule == "joueur1" or prev_winner is None:
        return 1
    if rule == "suivre":
        return prev_winner
    return 3 - prev_winner  # contre


def simulate(matches, rule, odds, *, reset_each_match):
    st = {"cycles": 0, "busted": 0, "staked": 0, "net": 0.0, "peak": 0.0, "max_dd": 0.0,
          "won_at": [0] * STEPS}
    step = 0

    def settle(delta):
        st["net"] += delta
        st["peak"] = max(st["peak"], st["net"])
        st["max_dd"] = max(st["max_dd"], st["peak"] - st["net"])

    for sets in matches:
        if reset_each_match:
            step = 0
        prev = None
        for winner in sets:
            if step == 0:
                st["cycles"] += 1
            stake = BASE * 2 ** step
            side = choose(rule, prev)
            st["staked"] += stake
            if winner == side:
                settle(stake * (odds - 1))
                st["won_at"][step] += 1
                step = 0
            else:
                settle(-stake)
                step += 1
                if step == STEPS:
                    st["busted"] += 1
                    step = 0
            prev = winner
    return st


def facts(matches):
    """(matchs, sets, taux de victoire du joueur 1, taux de répétition du vainqueur d'un set)."""
    n_sets = p1 = same = pairs = 0
    for sets in matches:
        n_sets += len(sets)
        p1 += sum(1 for w in sets if w == 1)
        for a, b in zip(sets, sets[1:]):
            pairs += 1
            same += a == b
    return len(matches), n_sets, p1 / n_sets, same / pairs


def _line(label, st):
    roi = st["net"] / st["staked"] * 100
    won = " / ".join(str(w) for w in st["won_at"])
    print(f"    {label:<8} cycles={st['cycles']:>6}  gagnés à 1/2/3/4 = {won:<22} ruinés={st['busted']:>4}  "
          f"misé={st['staked']:>12,} F  résultat={st['net']:>+12,.0f} F  rendement={roi:+.1f} %  "
          f"pire creux={st['max_dd']:,.0f} F".replace(",", " "))


def main(path):
    sys.stdout.reconfigure(encoding="utf-8")
    data = load(path)
    for venue, name in VENUES.items():
        matches = data[venue]
        n_m, n_s, p1, same = facts(matches)
        print(f"\n=== {name} : {n_m} matchs, {n_s} sets ===")
        print(f"  Joueur 1 gagne {p1 * 100:.1f} % des sets ; le vainqueur d'un set gagne aussi le "
              f"suivant dans {same * 100:.1f} % des cas")
        for odds in (1.85, 2.00):
            tag = "cote 1,85 (réelle)" if odds == 1.85 else "cote 2,00 (hypothèse sans marge)"
            for reset, lab in ((False, "Lecture B — sets consécutifs, à travers les matchs"),
                               (True, "Lecture A — le cycle repart à chaque match")):
                print(f"  [{tag}] {lab}")
                for rule in RULES:
                    _line(rule, simulate(matches, rule, odds, reset_each_match=reset))


if __name__ == "__main__":
    main(sys.argv[1])
