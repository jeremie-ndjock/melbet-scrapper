"""Tests du détecteur de changement (option A)."""
from __future__ import annotations

from decimal import Decimal

from collector.dedupe import ChangeDetector
from collector.normalize import SnapshotRow

GAME = 1


def row(g=1, t=1, param="0", sub_game_id=0, odds="1.500", blocked=False, is_center=False):
    return SnapshotRow(GAME, g, t, Decimal(param), sub_game_id,
                        Decimal(odds) if odds is not None else None,
                        blocked, is_center, None, None)


def as_dict(*rows):
    return {r.key: r for r in rows}


def test_first_sighting_writes_every_selection():
    d = ChangeDetector()
    changes = d.diff(GAME, as_dict(row(t=1), row(t=3)))
    assert {c.key for c in changes} == {(1, 1, Decimal(0), 0), (1, 3, Decimal(0), 0)}


def test_unchanged_selection_produces_no_row():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1, odds="1.500")))
    changes = d.diff(GAME, as_dict(row(t=1, odds="1.500")))
    assert changes == []


def test_changed_odds_is_reported():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1, odds="1.500")))
    changes = d.diff(GAME, as_dict(row(t=1, odds="1.800")))
    assert len(changes) == 1 and changes[0].odds == Decimal("1.800")


def test_changed_lock_without_odds_change_is_reported():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1, odds="1.500", blocked=False)))
    changes = d.diff(GAME, as_dict(row(t=1, odds="1.500", blocked=True)))
    assert len(changes) == 1 and changes[0].blocked is True


def test_disappeared_selection_is_reported_as_removed():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1), row(t=3)))
    changes = d.diff(GAME, as_dict(row(t=1)))  # t=3 a disparu
    assert len(changes) == 1
    removed = changes[0]
    assert removed.key == (1, 3, Decimal(0), 0) and removed.odds is None


def test_removed_selection_is_not_rewritten_on_later_cycles():
    """Une sélection retirée ne doit produire une ligne qu'une seule fois, pas à chaque cycle
    suivant tant qu'elle reste absente (sans quoi l'option A perdrait son intérêt sur un match
    long : chaque retrait précoce polluerait tous les cycles restants)."""
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1), row(t=3)))
    first_removal = d.diff(GAME, as_dict(row(t=1)))  # t=3 disparaît
    assert len(first_removal) == 1 and first_removal[0].key == (1, 3, Decimal(0), 0)

    for _ in range(5):  # t=3 reste absent pendant plusieurs cycles supplémentaires
        changes = d.diff(GAME, as_dict(row(t=1)))
        assert changes == []  # aucune nouvelle ligne pour une sélection déjà signalée comme retirée


def test_reappeared_selection_is_treated_as_new():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1)))
    d.diff(GAME, as_dict())  # disparaît
    changes = d.diff(GAME, as_dict(row(t=1, odds="2.000")))  # réapparaît
    assert len(changes) == 1 and changes[0].odds == Decimal("2.000")


def test_games_are_independent():
    d = ChangeDetector()
    d.diff(1, as_dict(row(t=1)))
    changes = d.diff(2, as_dict(row(t=1)))  # premier relevé d'un autre match
    assert len(changes) == 1


def test_preload_prevents_spurious_rewrite_of_known_state():
    d = ChangeDetector()
    d.preload(GAME, [row(t=1, odds="1.500")])
    changes = d.diff(GAME, as_dict(row(t=1, odds="1.500")))
    assert changes == []  # déjà connu (relu depuis la base) : pas de réécriture


def test_forget_clears_state():
    d = ChangeDetector()
    d.diff(GAME, as_dict(row(t=1)))
    d.forget(GAME)
    changes = d.diff(GAME, as_dict(row(t=1)))
    assert len(changes) == 1  # traité comme un premier relevé


def test_same_group_type_param_in_different_subgames_are_independent_selections():
    """Découvert avec AI Table Tennis (Memoire.md, section 27) : le Handicap du set 1 et le
    Handicap du set 2 partagent le même (g, t, param) mais ne sont PAS la même sélection — sans
    sub_game_id, la cote de l'un écraserait celle de l'autre."""
    d = ChangeDetector()
    set1 = row(g=2, t=7, sub_game_id=101, odds="1.500")
    set2 = row(g=2, t=7, sub_game_id=202, odds="1.900")
    changes = d.diff(GAME, as_dict(set1, set2))
    assert len(changes) == 2
    assert {c.key for c in changes} == {(2, 7, Decimal(0), 101), (2, 7, Decimal(0), 202)}


def test_a_subgame_selection_disappearing_does_not_affect_the_same_selection_in_another_subgame():
    d = ChangeDetector()
    set1 = row(g=2, t=7, sub_game_id=101, odds="1.500")
    set2 = row(g=2, t=7, sub_game_id=202, odds="1.900")
    d.diff(GAME, as_dict(set1, set2))
    changes = d.diff(GAME, as_dict(set2))  # le set 1 est terminé, sa cote disparaît

    assert len(changes) == 1
    removed = changes[0]
    assert removed.key == (2, 7, Decimal(0), 101) and removed.odds is None
