"""Tests de l'exécuteur de migrations."""
from __future__ import annotations

import shutil
from pathlib import Path

import asyncpg
import pytest

from collector.storage.migrate import DEFAULT_DIR, MigrationError, migrate

ALL_VERSIONS = [p.stem for p in sorted(DEFAULT_DIR.glob("*.sql"))]


async def test_applies_all_migrations_then_is_idempotent(dsn):
    first = await migrate(dsn)
    assert first == ALL_VERSIONS
    assert len(first) >= 4

    second = await migrate(dsn)  # rejouer ne fait rien
    assert second == []

    conn = await asyncpg.connect(dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM schema_migrations")
    finally:
        await conn.close()
    assert count == len(ALL_VERSIONS)


async def test_modified_migration_is_rejected(dsn):
    await migrate(dsn)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE schema_migrations SET checksum = 'altéré' WHERE version = $1", ALL_VERSIONS[0])
    finally:
        await conn.close()
    with pytest.raises(MigrationError, match="modifiée"):
        await migrate(dsn)


async def test_missing_migration_file_is_detected(dsn, tmp_path: Path):
    await migrate(dsn)
    partial = tmp_path / "partial"
    partial.mkdir()
    for path in sorted(DEFAULT_DIR.glob("*.sql"))[:-1]:  # sans la dernière
        shutil.copy(path, partial / path.name)
    with pytest.raises(MigrationError, match="fichier absent"):
        await migrate(dsn, partial)


async def test_out_of_order_migration_is_rejected(dsn, tmp_path: Path):
    # Migrations synthétiques : les vraies dépendent les unes des autres (clés étrangères).
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id int);", encoding="utf-8")
    (tmp_path / "003_c.sql").write_text("CREATE TABLE c (id int);", encoding="utf-8")
    await migrate(dsn, tmp_path)

    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (id int);", encoding="utf-8")  # ajoutée après coup
    with pytest.raises(MigrationError, match="antérieure"):
        await migrate(dsn, tmp_path)


async def test_failed_migration_is_rolled_back(dsn, tmp_path: Path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE a (id int);", encoding="utf-8")
    (tmp_path / "002_broken.sql").write_text("CREATE TABLE b (id int); SELECT 1/0;", encoding="utf-8")
    with pytest.raises(asyncpg.PostgresError):
        await migrate(dsn, tmp_path)

    conn = await asyncpg.connect(dsn)
    try:
        versions = [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")]
        table_b = await conn.fetchval("SELECT to_regclass('public.b')")
        table_a = await conn.fetchval("SELECT to_regclass('public.a')")
    finally:
        await conn.close()
    assert versions == ["001_ok"]      # la première est conservée
    assert table_a is not None
    assert table_b is None             # la seconde n'a laissé aucune trace


async def test_invalid_file_name_is_rejected(dsn, tmp_path: Path):
    (tmp_path / "schema.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="nom de migration invalide"):
        await migrate(dsn, tmp_path)


async def test_missing_migrations_directory_is_reported_clearly(dsn, tmp_path: Path):
    """Régression du vrai défaut de déploiement trouvé à l'étape 8 : l'image de production ne
    copiait pas `migrations/`, et le message d'erreur d'origine était le seul indice disponible
    dans les journaux du conteneur. On verrouille ici qu'il reste clair et distinct des autres
    erreurs de migration (pas une simple exception non gérée)."""
    absent = tmp_path / "n_existe_pas"
    with pytest.raises(MigrationError, match="dossier de migrations introuvable"):
        await migrate(dsn, absent)
