"""Exécuteur de migrations SQL.

Applique dans l'ordre les fichiers ``migrations/NNN_nom.sql`` qui ne l'ont pas encore été.

Garanties :
- chaque migration s'exécute dans sa propre transaction (tout ou rien) ;
- un verrou consultatif empêche deux exécuteurs de migrer en même temps ;
- la somme de contrôle de chaque fichier appliqué est mémorisée : modifier une migration déjà
  appliquée est une erreur (on écrit une nouvelle migration à la place) ;
- rejouer l'exécuteur sur une base à jour ne fait rien.

Usage : ``python -m collector.storage.migrate`` (lit DATABASE_URL).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from pathlib import Path

import asyncpg

log = logging.getLogger("collector.migrate")

# Emplacement par défaut : <racine du projet>/migrations (src/collector/storage/migrate.py -> parents[3]).
DEFAULT_DIR = Path(os.environ.get("MIGRATIONS_DIR") or Path(__file__).resolve().parents[3] / "migrations")
ADVISORY_LOCK_ID = 727_001
_NAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Erreur de cohérence entre les fichiers de migration et la base."""


def discover(directory: Path = DEFAULT_DIR) -> list[tuple[str, str, str]]:
    """Retourne [(version, checksum, sql)] triés par version."""
    if not directory.is_dir():
        raise MigrationError(f"dossier de migrations introuvable : {directory}")
    found: list[tuple[str, str, str]] = []
    for path in sorted(directory.glob("*.sql")):
        if not _NAME_RE.match(path.name):
            raise MigrationError(f"nom de migration invalide : {path.name} (attendu NNN_nom.sql)")
        sql = path.read_text(encoding="utf-8")
        found.append((path.stem, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql))
    return found


async def migrate(dsn: str, directory: Path = DEFAULT_DIR) -> list[str]:
    """Applique les migrations en attente. Retourne la liste des versions appliquées."""
    files = discover(directory)
    conn = await asyncpg.connect(dsn)
    newly_applied: list[str] = []
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_ID)
        await conn.execute(_CREATE_TABLE)

        applied = {r["version"]: r["checksum"] for r in await conn.fetch("SELECT version, checksum FROM schema_migrations")}
        on_disk = {version: checksum for version, checksum, _ in files}

        for version, checksum in applied.items():
            if version not in on_disk:
                raise MigrationError(f"migration déjà appliquée mais fichier absent : {version}")
            if on_disk[version] != checksum:
                raise MigrationError(f"la migration {version} a été modifiée après son application")

        pending = [f for f in files if f[0] not in applied]
        if pending and applied and pending[0][0] < max(applied):
            raise MigrationError(f"migration {pending[0][0]} antérieure à une migration déjà appliquée ({max(applied)})")

        for version, checksum, sql in pending:
            log.info("application de la migration %s", version)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)", version, checksum)
            newly_applied.append(version)
        if not pending:
            log.info("base à jour (%d migrations appliquées)", len(applied))
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_ID)
        finally:
            await conn.close()
    return newly_applied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL n'est pas défini")
        return 2
    try:
        applied = asyncio.run(migrate(dsn))
    except (MigrationError, asyncpg.PostgresError, OSError) as exc:
        log.error("échec des migrations : %s", type(exc).__name__ + ": " + str(exc).splitlines()[0])
        return 1
    log.info("terminé : %d migration(s) appliquée(s)", len(applied))
    return 0


if __name__ == "__main__":
    sys.exit(main())
