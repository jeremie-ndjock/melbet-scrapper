"""Tests de l'enveloppe en ligne de commande de l'exécuteur de migrations (`main()`), jamais
couverte jusqu'ici. Tout est doublé : aucune vraie connexion, aucun vrai `asyncio.run`."""
from __future__ import annotations

import asyncpg
import pytest

from collector.storage import migrate as migrate_module
from collector.storage.migrate import MigrationError


def test_main_returns_2_when_database_url_is_missing(monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    code = migrate_module.main()

    assert code == 2


def test_main_returns_1_and_logs_on_migration_error(monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    def fake_run(coro):
        coro.close()
        raise MigrationError("dossier de migrations introuvable : /app/migrations")

    monkeypatch.setattr(migrate_module.asyncio, "run", fake_run)

    with caplog.at_level("ERROR", logger="collector.migrate"):
        code = migrate_module.main()

    assert code == 1
    assert any("dossier de migrations introuvable" in r.message for r in caplog.records)


def test_main_returns_1_on_postgres_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    def fake_run(coro):
        coro.close()
        raise asyncpg.PostgresError("connexion refusée")

    monkeypatch.setattr(migrate_module.asyncio, "run", fake_run)

    assert migrate_module.main() == 1


def test_main_returns_0_and_logs_count_on_success(monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    def fake_run(coro):
        coro.close()
        return ["005_allow_tied_results"]

    monkeypatch.setattr(migrate_module.asyncio, "run", fake_run)

    with caplog.at_level("INFO", logger="collector.migrate"):
        code = migrate_module.main()

    assert code == 0
    assert any("1 migration" in r.message for r in caplog.records)
