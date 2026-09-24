"""Backtest de la martingale (1000/2000/4000/8000 F, 4 manches au plus) sur les vraies manches
Mortal Kombat X et Mortal Kombat 3 (Memoire.md, section 35).

Lecture A : un cycle par match, sur les manches 1 à 4 (arrêt au premier gain ou après 4 pertes).
Lecture B : une mise sur chaque manche, en continu ; le cycle repart à 1000 F après un gain ou
            après 4 pertes consécutives.
Choix du combattant : favori (cote la plus basse), outsider, ou toujours le joueur 1.

Cote utilisée : dernière cote non suspendue du marché « Victoire dans le Round » (g=1050) avant sa
fermeture. Ce marché se ferme au début de la manche et bouge à peine avant : aucune fuite
d'information sur l'issue.

Export des données (sur le VPS, puis rapatrier le fichier) :

    docker compose exec -T db psql -U collector -d odds -c "\\copy (
      WITH q AS (
        SELECT DISTINCT ON (o.game_id, o.round_no, o.t) o.game_id, o.round_no, o.t, o.odds
        FROM odds_snapshots o
        WHERE o.g = 1050 AND o.league_id IN (1252965, 2282406) AND o.odds IS NOT NULL
          AND NOT o.blocked AND o.round_no IS NOT NULL
        ORDER BY o.game_id, o.round_no, o.t, o.ts_server DESC)
      SELECT e.league_id, rr.game_id, e.start_ts, rr.round_no, rr.winner,
             q1.odds AS odds_p1, q2.odds AS odds_p2
      FROM round_results rr JOIN events e ON e.game_id = rr.game_id
      LEFT JOIN q q1 ON q1.game_id = rr.game_id AND q1.round_no = rr.round_no AND q1.t = 2140
      LEFT JOIN q q2 ON q2.game_id = rr.game_id AND q2.round_no = rr.round_no AND q2.t = 2141
      WHERE e.league_id IN (1252965, 2282406)
      ORDER BY e.league_id, e.start_ts, rr.game_id, rr.round_no
    ) TO STDOUT WITH CSV HEADER" > rounds_mk.csv

Usage : ``python scripts/backtest_martingale_mk.py rounds_mk.csv``
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict

BASE = 1000
STEPS = 4
LEAGUES = {1252965: "Mortal Kombat X", 2282406: "Mortal Kombat 3"}
RULES = ("favori", "outsider", "joueur1")


def load(path):
    """Manches avec vainqueur et deux cotes connues, groupées par match, dans l'ordre."""
    games = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["winner"] not in ("1", "2") or not r["odds_p1"] or not r["odds_p2"]:
                continue
            games[(int(r["league_id"]), r["start_ts"], int(r["game_id"]))].append({
                "round": int(r["round_no"]), "winner": int(r["winner"]),
                "o1": float(r["odds_p1"]), "o2": float(r["odds_p2"]),
            })
    for rounds in games.values():
        rounds.sort(key=lambda x: x["round"])
    return dict(sorted(games.items()))


def pick(rnd, rule):
    if rule == "favori":
        return 1 if rnd["o1"] <= rnd["o2"] else 2
    if rule == "outsider":
        return 2 if rnd["o1"] <= rnd["o2"] else 1
    return 1


def new_stats():
    return {"cycles": 0, "won_at": [0] * STEPS, "busted": 0, "staked": 0.0, "net": 0.0,
            "peak": 0.0, "max_dd": 0.0, "bets": 0}


def _settle(st, delta):
    st["net"] += delta
    st["peak"] = max(st["peak"], st["net"])
    st["max_dd"] = max(st["max_dd"], st["peak"] - st["net"])


def run_cycle(st, rounds, rule):
    """Joue un cycle sur les manches fournies (au plus STEPS). Retourne le nombre de manches
    consommées."""
    st["cycles"] += 1
    for k, rnd in enumerate(rounds[:STEPS]):
        stake = BASE * 2 ** k
        side = pick(rnd, rule)
        odds = rnd["o1"] if side == 1 else rnd["o2"]
        st["staked"] += stake
        st["bets"] += 1
        if rnd["winner"] == side:
            _settle(st, stake * (odds - 1))
            st["won_at"][k] += 1
            return k + 1
        _settle(st, -stake)
    st["busted"] += 1
    return STEPS


def strategy_a(games, rule, league):
    st = new_stats()
    for (lg, _, _), rounds in games.items():
        if lg != league:
            continue
        first4 = [r for r in rounds if r["round"] <= STEPS]
        if [r["round"] for r in first4] != list(range(1, STEPS + 1)):
            continue  # manches 1 à 4 incomplètes (cotes non captées) : match ignoré
        run_cycle(st, first4, rule)
    return st


def strategy_b(games, rule, league):
    st = new_stats()
    seq = [r for (lg, _, _), rounds in games.items() if lg == league for r in rounds]
    i = 0
    while i < len(seq):
        i += run_cycle(st, seq[i:], rule)
    return st


def market_facts(games, league):
    """(manches, taux de victoire du favori, probabilité implicite moyenne du favori marge
    retirée, marge moyenne du bookmaker)."""
    n = fav_wins = 0
    implied_fav = overround = 0.0
    for (lg, _, _), rounds in games.items():
        if lg != league:
            continue
        for r in rounds:
            n += 1
            fav = 1 if r["o1"] <= r["o2"] else 2
            fav_wins += r["winner"] == fav
            s = 1 / r["o1"] + 1 / r["o2"]
            overround += s
            implied_fav += (1 / min(r["o1"], r["o2"])) / s
    return n, fav_wins / n, implied_fav / n, overround / n - 1


def _report(label, st):
    roi = st["net"] / st["staked"] * 100 if st["staked"] else 0
    won = " / ".join(str(w) for w in st["won_at"])
    print(f"  {label:<9} cycles={st['cycles']:>5}  gagnés à 1/2/3/4 = {won:<17} "
          f"ruinés={st['busted']:>4}  misé={st['staked']:>12,.0f} F  résultat={st['net']:>+12,.0f} F  "
          f"rendement={roi:+.1f} %  pire creux={st['max_dd']:,.0f} F".replace(",", " "))


def main(path):
    sys.stdout.reconfigure(encoding="utf-8")
    games = load(path)
    for league, name in LEAGUES.items():
        n, fav_rate, fav_implied, margin = market_facts(games, league)
        print(f"\n=== {name} ({n} manches avec cote et vainqueur) ===")
        print(f"  Marge moyenne du bookmaker : {margin * 100:.1f} %")
        print(f"  Le favori gagne {fav_rate * 100:.1f} % des manches "
              f"(probabilité implicite moyenne, marge retirée : {fav_implied * 100:.1f} %)")
        print(" Lecture A — un cycle par match, manches 1 à 4 :")
        for rule in RULES:
            _report(rule, strategy_a(games, rule, league))
        print(" Lecture B — une mise sur chaque manche, en continu :")
        for rule in RULES:
            _report(rule, strategy_b(games, rule, league))


if __name__ == "__main__":
    main(sys.argv[1])
