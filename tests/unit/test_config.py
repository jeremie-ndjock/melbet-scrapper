"""Vérifie que les fichiers de configuration se chargent et ont la forme attendue."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from collector.config import load_leagues, load_settings


def test_leagues_yaml_contains_the_two_validated_leagues():
    leagues = {league.id: league.name for league in load_leagues()}
    assert leagues == {1252965: "Mortal Kombat X", 2282406: "Mortal Kombat 3"}


def test_settings_yaml_has_required_transport_fields():
    settings = load_settings()
    t = settings.transport
    assert t.user_agent == "OddsCollector/1.0"  # identification fixe, jamais de rotation
    assert t.max_requests_per_second > 0
    assert settings.site_params == {"fcountry": "84", "gr": "2147", "lng": "fr", "ref": "8"}
    assert settings.poll_interval_seconds == 5


def test_duplicate_league_id_is_rejected(tmp_path: Path):
    bad = tmp_path / "leagues.yaml"
    bad.write_text(yaml.safe_dump({"leagues": [
        {"id": 1, "name": "A", "sport_id": 103},
        {"id": 1, "name": "B", "sport_id": 103},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="double"):
        load_leagues(bad)


def test_invalid_config_file_is_rejected(tmp_path: Path):
    bad = tmp_path / "leagues.yaml"
    bad.write_text("- juste une liste, pas un dictionnaire", encoding="utf-8")
    with pytest.raises(ValueError, match="invalide"):
        load_leagues(bad)
