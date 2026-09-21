"""Tests de l'analyse du score et du découpage en fenêtres du rattrapage d'historique."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from collector.results import ResultParseError, align_down, iter_windows, parse_score


def test_parse_score_mortal_kombat_x_without_mercy_suffix():
    parsed = parse_score("5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)")
    assert (parsed.final1, parsed.final2, parsed.winner) == (5, 0, 1)
    assert len(parsed.rounds) == 5
    assert [r.finish_code for r in parsed.rounds] == ["F", "R", "R", "R", "F"]
    assert all(r.winner == 1 for r in parsed.rounds)
    assert all(r.mercy_p1 is None and r.mercy_p2 is None for r in parsed.rounds)


def test_parse_score_mortal_kombat_3_with_mercy_suffix():
    raw = "5:3(0:1 F ,M- / M-; 1:0 R ,M- / M-; 0:1 F ,M- / M-; 1:0 R ,M- / M-; 0:1 B ,M- / M-; 1:0 Ba ,M- / M-; 1:0 F ,M- / M-; 1:0 R ,M- / M-)"
    parsed = parse_score(raw)
    assert (parsed.final1, parsed.final2, parsed.winner) == (5, 3, 1)
    assert len(parsed.rounds) == 8
    assert parsed.rounds[5].finish_code == "Ba"  # Babality
    assert all(r.mercy_p1 is False and r.mercy_p2 is False for r in parsed.rounds)


@pytest.mark.parametrize("code, fragment", [
    ("R", "1:0 R"),
    ("F", "1:0 F"),
    ("B", "1:0 B"),
    ("Ba", "1:0 Ba ,M- / M-"),   # Babality
    ("Fr", "0:1 Fr ,M- / M-"),   # Amitié (Friendship)
    ("An", "1:0 An ,M- / M-"),   # Animality
    ("Hk", "1:0 Hk ,M- / M-"),   # Hara-Kiri
])
def test_all_known_finish_codes_are_parsed(code, fragment):
    parsed = parse_score(f"1:0({fragment})")
    assert parsed.rounds[0].finish_code == code


def test_mercy_flag_can_be_true_for_either_player():
    parsed = parse_score("1:5(0:1 F ,M- / M+)")  # joueur 2 a fait Mercy
    assert parsed.rounds[0].mercy_p1 is False
    assert parsed.rounds[0].mercy_p2 is True


def test_unrecognisable_round_is_skipped_not_fatal():
    parsed = parse_score("2:0(1:0 F;???;1:0 R)")
    assert len(parsed.rounds) == 2  # le fragment illisible est ignoré, le reste est conservé


def test_totally_malformed_score_raises():
    with pytest.raises(ResultParseError, match="méconnaissable"):
        parse_score("pas un score du tout")


def test_tied_final_score_raises():
    with pytest.raises(ResultParseError, match="égalité"):
        parse_score("3:3(1:0 R;0:1 R;1:0 R;0:1 R;1:0 R;0:1 R)")


def test_align_down_rounds_to_previous_5_minutes():
    ts = datetime(2026, 9, 21, 12, 7, 42, tzinfo=timezone.utc)
    assert align_down(ts) == datetime(2026, 9, 21, 12, 5, 0, tzinfo=timezone.utc)


def test_iter_windows_covers_the_full_range_without_gap_or_overlap():
    end = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
    windows = iter_windows(end=end, days_back=7)
    assert windows[0][1] == end + timedelta(minutes=5)  # borne haute alignée, incluant l'instant présent
    for (a, b) in windows:
        assert b - a <= timedelta(days=2)
        assert int(a.timestamp()) % 300 == 0 and int(b.timestamp()) % 300 == 0
    # Les fenêtres sont classées de la plus récente à la plus ancienne : le début de l'une doit
    # toucher exactement la fin de la suivante (plus ancienne), sans trou ni recouvrement.
    for current, older in zip(windows, windows[1:]):
        assert current[0] == older[1]
    expected_start_limit = align_down(end) + timedelta(minutes=5) - timedelta(days=7)
    assert windows[-1][0] == expected_start_limit


def test_iter_windows_respects_two_day_cap_even_for_a_short_range():
    windows = iter_windows(end=datetime.now(timezone.utc), days_back=1)
    assert len(windows) == 1
    assert windows[0][1] - windows[0][0] <= timedelta(days=1) + timedelta(minutes=5)
