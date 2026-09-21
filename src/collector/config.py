"""Chargement de la configuration depuis config/*.yaml (aucun secret : ceux-ci restent dans .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .transport.http import RetryConfig  # réutilisé tel quel, jamais redéfini

# src/collector/config.py -> parents[2] = racine du projet.
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")


@dataclass(frozen=True)
class League:
    id: int
    name: str
    sport_id: int


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int = 5
    recovery_seconds: float = 30.0


@dataclass(frozen=True)
class TransportConfig:
    base_url: str
    user_agent: str
    timeout_seconds: float = 10.0
    max_requests_per_second: float = 1.0
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: BreakerConfig = field(default_factory=BreakerConfig)


@dataclass(frozen=True)
class Settings:
    transport: TransportConfig
    site_params: dict[str, str]
    poll_interval_seconds: float = 5.0


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"fichier de configuration invalide : {path}")
    return data


def load_leagues(path: Path | None = None) -> list[League]:
    data = _read(path or CONFIG_DIR / "leagues.yaml")
    leagues = [League(id=int(x["id"]), name=str(x["name"]), sport_id=int(x["sport_id"])) for x in data["leagues"]]
    if len({lg.id for lg in leagues}) != len(leagues):
        raise ValueError("identifiants de ligue en double dans leagues.yaml")
    return leagues


def load_settings(path: Path | None = None) -> Settings:
    data = _read(path or CONFIG_DIR / "settings.yaml")
    t = data["transport"]
    transport = TransportConfig(
        base_url=str(t["base_url"]).rstrip("/"),
        user_agent=str(t["user_agent"]),
        timeout_seconds=float(t.get("timeout_seconds", 10)),
        max_requests_per_second=float(t.get("max_requests_per_second", 1.0)),
        retry=RetryConfig(**t.get("retry", {})),
        circuit_breaker=BreakerConfig(**t.get("circuit_breaker", {})),
    )
    return Settings(
        transport=transport,
        site_params={str(k): str(v) for k, v in data["site_params"].items()},
        poll_interval_seconds=float(data.get("scheduler", {}).get("poll_interval_seconds", 5)),
    )
