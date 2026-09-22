"""Tests de la résolution des portraits de combattants : tolérant à toute absence (pas de mapping,
mapping illisible, image manquante) — une annonce pré-match sans image ne doit jamais planter."""
from __future__ import annotations

import importlib
import json

from collector import fighter_images


def _reload_with(tmp_path, monkeypatch):
    monkeypatch.setenv("FIGHTER_IMAGES_DIR", str(tmp_path))
    importlib.reload(fighter_images)
    return fighter_images


def test_image_path_resolves_a_known_fighter(tmp_path, monkeypatch):
    (tmp_path / "goro.png").write_bytes(b"fake-image-bytes")
    (tmp_path / "_mapping.json").write_text(json.dumps({"Goro": "goro.png"}), encoding="utf-8")
    mod = _reload_with(tmp_path, monkeypatch)

    path = mod.image_path("Goro")

    assert path is not None and path.name == "goro.png" and path.is_file()


def test_image_path_returns_none_for_an_unknown_fighter(tmp_path, monkeypatch):
    (tmp_path / "_mapping.json").write_text(json.dumps({"Goro": "goro.png"}), encoding="utf-8")
    mod = _reload_with(tmp_path, monkeypatch)

    assert mod.image_path("Combattant Inconnu") is None


def test_image_path_returns_none_when_the_mapped_file_is_missing(tmp_path, monkeypatch):
    # Le mapping référence un fichier qui n'existe pas (supprimé, mal copié...).
    (tmp_path / "_mapping.json").write_text(json.dumps({"Goro": "absent.png"}), encoding="utf-8")
    mod = _reload_with(tmp_path, monkeypatch)

    assert mod.image_path("Goro") is None


def test_missing_mapping_file_gives_an_empty_lookup_without_raising(tmp_path, monkeypatch):
    mod = _reload_with(tmp_path, monkeypatch)  # aucun _mapping.json créé

    assert mod.image_path("Goro") is None


def test_unreadable_mapping_json_gives_an_empty_lookup_without_raising(tmp_path, monkeypatch, caplog):
    (tmp_path / "_mapping.json").write_text("{ceci n'est pas du json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        mod = _reload_with(tmp_path, monkeypatch)

    assert mod.image_path("Goro") is None
    assert any("illisible" in r.message for r in caplog.records)


def test_accented_fighter_names_round_trip_correctly(tmp_path, monkeypatch):
    """Défaut réel rencontré en préparant les images (étape « fil de match ») : un nom accentué
    mal encodé lors d'une commande shell ne correspondait plus au nom exact renvoyé par l'API."""
    (tmp_path / "predateur.png").write_bytes(b"x")
    (tmp_path / "_mapping.json").write_text(
        json.dumps({"Prédateur": "predateur.png"}, ensure_ascii=False), encoding="utf-8")
    mod = _reload_with(tmp_path, monkeypatch)

    assert mod.image_path("Prédateur") is not None
