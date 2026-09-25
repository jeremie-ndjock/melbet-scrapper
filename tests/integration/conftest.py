"""Fixtures des tests d'intégration.

Nécessite DATABASE_URL (fourni par docker-compose) vers une base PostgreSQL + TimescaleDB.

- ``dsn``  : base vierge (non migrée), pour tester l'exécuteur de migrations lui-même ;
- ``db``   : connexion à une base migrée. Les migrations ne sont jouées qu'une fois par session,
             dans une base modèle ; chaque test en reçoit une copie (rapide) puis la supprime.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from collector.storage.migrate import migrate

ADMIN_DSN = os.environ.get("DATABASE_URL")


def _dsn_for(dbname: str) -> str:
    return urlunsplit(urlsplit(ADMIN_DSN)._replace(path=f"/{dbname}"))


async def _admin_execute(sql: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def _run(coro) -> None:
    """Exécute une coroutine dans une boucle dédiée (fixtures synchrones de session)."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="session")
def template_db():
    """Nom d'une base modèle déjà migrée (créée une fois, supprimée en fin de session)."""
    if not ADMIN_DSN:
        pytest.skip("DATABASE_URL non défini (lancer via docker compose)")
    name = f"tpl_{uuid.uuid4().hex[:12]}"
    _run(_admin_execute(f'CREATE DATABASE "{name}"'))
    _run(migrate(_dsn_for(name)))
    try:
        yield name
    finally:
        _run(_admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def dsn():
    """DSN d'une base vierge (non migrée), supprimée à la fin du test."""
    if not ADMIN_DSN:
        pytest.skip("DATABASE_URL non défini (lancer via docker compose)")
    name = f"test_{uuid.uuid4().hex[:12]}"
    await _admin_execute(f'CREATE DATABASE "{name}"')
    try:
        yield _MaskedDsn(_dsn_for(name))
    finally:
        await _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


class _MaskedDsn(str):
    """DSN utilisable tel quel, mais dont l'affichage masque le mot de passe : pytest affiche la
    valeur des paramètres d'un test en échec, et elle ne doit jamais apparaître dans un journal."""

    def __repr__(self) -> str:
        return "'<DSN masqué>'"


async def _create_from_template(name: str, template: str, attempts: int = 30) -> None:
    """Copie la base modèle. PostgreSQL refuse de copier une base qui a une session ouverte, or
    l'ordonnanceur de tâches de TimescaleDB s'y connecte de lui-même : on coupe cette session
    parasite et on réessaie brièvement (il se reconnecte après quelques secondes)."""
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        for attempt in range(attempts):
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                template,
            )
            try:
                await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
                return
            except asyncpg.ObjectInUseError:
                await asyncio.sleep(0.3)
        raise RuntimeError(f"impossible de copier la base modèle {template} après {attempts} essais")
    finally:
        await conn.close()


@pytest.fixture
async def db(template_db):
    """Connexion à une copie neuve de la base modèle migrée."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    await _create_from_template(name, template_db)
    conn = await asyncpg.connect(_dsn_for(name))
    try:
        yield conn
    finally:
        await conn.close()
        await _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def db_dsn(template_db):
    """DSN d'une copie neuve de la base modèle migrée (pour le code qui ouvre lui-même sa
    connexion, comme l'extraction en lecture seule de `forecasting`)."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    await _create_from_template(name, template_db)
    try:
        yield _MaskedDsn(_dsn_for(name))
    finally:
        await _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def db_pool(template_db):
    """Petit pool de connexions vers une copie neuve de la base modèle migrée (pour le
    Scheduler, qui utilise `pool.acquire()` plutôt qu'une connexion unique)."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    await _create_from_template(name, template_db)
    pool = await asyncpg.create_pool(_dsn_for(name), min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()
        await _admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
