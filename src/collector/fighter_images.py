"""Portraits des combattants, pour l'annonce pré-match (demandé le 2026-09-22).

Les images elles-mêmes ne sont pas générées : l'utilisateur les a fournies, une par combattant,
nommées exactement comme dans ``_mapping.json`` (clé = nom affiché par le site, exactement comme
``Game.opponent1.display_name`` — voir ``normalize.py``). Un combattant sans image connue n'est
pas une erreur : l'annonce pré-match retombe simplement sur du texte seul (voir pre_match.py).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("collector.fighter_images")

# src/collector/fighter_images.py -> parents[2] = racine du projet.
ASSETS_DIR = Path(os.environ.get("FIGHTER_IMAGES_DIR")
                  or Path(__file__).resolve().parents[2] / "assets" / "fighters")


def _load_mapping(directory: Path) -> dict[str, str]:
    mapping_file = directory / "_mapping.json"
    if not mapping_file.is_file():
        log.info("aucun mapping d'images de combattants trouvé (%s) : annonces pré-match en texte seul", mapping_file)
        return {}
    try:
        data = json.loads(mapping_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("mapping des images de combattants illisible (%s) : %s", mapping_file, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("mapping des images de combattants au format inattendu (%s)", mapping_file)
        return {}
    return {str(k): str(v) for k, v in data.items()}


_MAPPING = _load_mapping(ASSETS_DIR)


def image_path(fighter_name: str) -> Path | None:
    """Chemin de l'image d'un combattant, ou ``None`` si aucune image connue ou si le fichier a
    disparu depuis (jamais une exception : une image manquante n'interrompt jamais l'annonce)."""
    filename = _MAPPING.get(fighter_name)
    if filename is None:
        return None
    path = ASSETS_DIR / filename
    return path if path.is_file() else None
