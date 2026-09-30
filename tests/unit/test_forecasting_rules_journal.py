"""Règle prudente figée à l'avance, journal des prédictions et bilan quotidien."""
from __future__ import annotations

import pandas as pd
import pytest

from forecasting import journal, rules
from forecasting.targets import MK3, MKX

AFTER = (rules.JUDGED_FROM + pd.Timedelta(hours=2)).isoformat()
BEFORE = (rules.JUDGED_FROM - pd.Timedelta(hours=2)).isoformat()
FAIR = [0.45, 0.38, 0.09, 0.03, 0.02, 0.01, 0.02]
ODDS = [2.10, 2.45, 10.0, 30.0, 45.0, 90.0, 40.0]


def test_rule_constants_are_the_registered_ones():
    """Garde-fou contre une modification silencieuse de la règle après son enregistrement."""
    assert (rules.ALLOWED, rules.MIN_EDGE, rules.STAKE) == (("R", "F", "B"), 0.03, 1000.0)
    assert rules.JUDGED_FROM == pd.Timestamp("2026-09-29 00:00", tz="UTC")


def test_pick_only_frequent_classes_with_a_clear_edge():
    model = [0.45, 0.43, 0.09, 0.01, 0.01, 0.00, 0.01]  # F : +5 pts
    assert rules.pick(model, FAIR, ODDS) == (1, 2.45)
    small = [0.46, 0.39, 0.09, 0.03, 0.01, 0.01, 0.01]  # +1 pt seulement
    assert rules.pick(small, FAIR, ODDS) is None
    rare = [0.40, 0.33, 0.09, 0.15, 0.01, 0.01, 0.01]  # gros avantage… sur Babality : exclu
    assert rules.pick(rare, FAIR, ODDS) is None
    assert rules.pick(None, FAIR, ODDS) is None


def test_judge_verdicts():
    assert rules.judge([])["verdict"] == "en attente de paris"
    few = rules.judge([{"game_id": i, "profit": 1450.0} for i in range(10)])
    assert few["n_paris"] == 10 and few["verdict"].startswith("en cours")
    winning = [{"game_id": i, "profit": 1450.0 if i % 2 else -1000.0} for i in range(600)]
    assert rules.judge(winning)["verdict"] == "avantage démontré"
    losing = [{"game_id": i, "profit": -1000.0 if i % 3 else 1450.0} for i in range(600)]
    assert rules.judge(losing)["verdict"].startswith("règle perdante")


def _rec(ts, **kw):
    base = {"ts": ts, "league": MKX, "game_id": 1, "round": 1, "p1": 0.6, "mkt_p1": 0.55, "winner": 1}
    return {**base, **kw}


def test_summarize_compares_model_and_market_on_the_same_rounds():
    recs = [
        _rec(AFTER, p1=0.6, mkt_p1=0.45, winner=1),   # modèle juste, marché faux
        _rec(AFTER, p1=0.6, mkt_p1=0.55, winner=2),   # les deux faux
        _rec(AFTER, mkt_p1=None, winner=1),           # sans cote : exclue de la comparaison
        _rec(AFTER, league=MK3, p1=0.3, mkt_p1=0.4, winner=2, P_finish=[0.5, 0.3, 0.2, 0, 0, 0, 0],
             mkt_P_finish=FAIR, finish_real="R"),
        _rec(AFTER, dur_line=20.5, p_over=0.6, mkt_over=0.45, seconds_real=25.0),
    ]
    s = journal.summarize(recs)
    # 3 manches MKX avec cote : la 1re, la 2e, et celle de la durée (valeurs par défaut, les deux justes)
    assert s[f"vainqueur_{MKX}"]["n"] == 3 and s[f"vainqueur_{MKX}"]["modele"] == pytest.approx(2 / 3)
    assert s[f"vainqueur_{MKX}"]["marche"] == pytest.approx(1 / 3)
    assert s[f"vainqueur_{MK3}"] == {**s[f"vainqueur_{MK3}"], "n": 1, "modele": 1.0, "marche": 1.0}
    assert s[f"finish_{MK3}"]["n"] == 1 and s[f"finish_{MK3}"]["modele"] == 1.0
    assert s[f"duree_{MKX}"]["n"] == 1 and s[f"duree_{MKX}"]["modele"] == 1.0 and s[f"duree_{MKX}"]["marche"] == 0.0


def test_rule_bets_only_after_registration_and_with_real_odds():
    model = [0.45, 0.43, 0.09, 0.01, 0.01, 0.00, 0.01]
    recs = [
        _rec(BEFORE, league=MK3, P_finish=model, mkt_P_finish=FAIR, odds_finish=ODDS, finish_real="F"),
        _rec(AFTER, league=MK3, game_id=2, P_finish=model, mkt_P_finish=FAIR, odds_finish=ODDS, finish_real="F"),
        _rec(AFTER, league=MK3, game_id=3, P_finish=model, mkt_P_finish=FAIR, odds_finish=ODDS, finish_real="R"),
        _rec(AFTER, league=MK3, game_id=4, P_finish=model, mkt_P_finish=FAIR, odds_finish=None, finish_real="F"),
    ]
    bets = journal.rule_bets(recs)
    assert [b["game_id"] for b in bets] == [2, 3]
    assert [b["profit"] for b in bets] == pytest.approx([1450.0, -1000.0])


def test_daily_report_text_and_empty_day(tmp_path):
    path = tmp_path / "log.jsonl"
    day = AFTER[:10]
    journal.append(path, _rec(AFTER, p1=0.6, mkt_p1=0.45, winner=1))
    path.open("a", encoding="utf-8").write('{"ligne tronquée\n')
    recs = journal.read(path)
    assert len(recs) == 1
    text = journal.daily_report(recs, day)
    assert text.startswith(f"📈 BILAN DES PRÉDICTIONS — {pd.Timestamp(day).strftime('%d/%m/%Y')}")
    assert "modèle 100,0 % juste, favori du marché 0,0 %" in text
    assert "Règle prudente" in text and "ne pas parier" in text
    assert "aucune manche résolue" in text  # finish / durée absents ce jour-là
    assert journal.daily_report(recs, "2020-01-01") is None
    assert journal.read(tmp_path / "absent.jsonl") == []


def test_daily_text_keeps_commas_and_uses_french_numbers():
    """Régression : un ``.replace(",", " ")`` global effaçait les virgules du texte (bilan du 28/09)."""
    summary = {k: journal._block(2147, 1207, 1241, 1454.0, 1441.0)
               for k in (f"vainqueur_{MKX}", f"vainqueur_{MK3}", f"finish_{MK3}", f"duree_{MKX}")}
    cumulative = {"n_paris": 194, "resultat": -63602.0, "rendement": -0.3278, "ic_bas": -0.5751, "ic_haut": -0.0317,
                  "verdict": "en cours (194/500 paris minimum avant verdict)"}
    text = journal.format_daily("2026-09-29", summary, [{"profit": -63602.0}], cumulative)
    assert "(2\u00a0147 manches) : modèle 56,2 % juste, favori du marché 57,8 %" in text
    assert "(log-loss 0,677 contre 0,671)" in text
    assert "(finish R/F/B, avantage ≥ 3 pts, mise fictive 1\u00a0000 F) : 1 paris ce jour, -63\u00a0602 F" in text
    assert "194 paris, -63\u00a0602 F, rendement -32,8 % [-57,5 % ; -3,2 %] — en cours" in text
    assert "  " not in text.replace("\n   Cumul", "")  # plus aucune double espace laissée par une virgule effacée
    assert journal._num(1234.5, 1, True) == "+1\u00a0234,5"
