"""Tests du point d'entrée de production (`collector.main`), jamais couvert jusqu'ici.

Tout est doublé (aucun réseau, aucune vraie base) : on vérifie seulement le câblage — ordre des
opérations au démarrage (migrations avant tout accès à la base), fermeture propre des ressources
même en cas d'erreur, code de sortie selon qu'un blocage a eu lieu, et l'avertissement quand aucun
canal d'alerte n'est configuré."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from collector import main as main_module


def _settings():
    return SimpleNamespace(
        transport=SimpleNamespace(
            base_url="https://example.test", user_agent="OddsCollector/1.0",
            timeout_seconds=10.0, max_requests_per_second=1.0,
            retry=SimpleNamespace(), circuit_breaker=SimpleNamespace(failure_threshold=5, recovery_seconds=30.0),
        ),
        site_params={}, legacy_site_params={},
        dictionary=SimpleNamespace(base_url="https://cdn.example.test", refresh_interval_hours=6.0),
        results=SimpleNamespace(backfill_days=90, reconcile_lookback_hours=2.0, reconcile_interval_minutes=5.0),
        observability=SimpleNamespace(metrics_port=9100),
        alerting=SimpleNamespace(cooldown_seconds=1800.0),
        poll_interval_seconds=5.0,
    )


@dataclass
class _FakeMetrics:
    blocked: bool = False


class _FakeScheduler:
    """Remplace Scheduler : n'appelle jamais réellement http/db, garde juste ce qu'on veut inspecter."""
    instances: list["_FakeScheduler"] = []

    def __init__(self, *, http, cdn_http, db_pool, leagues, site_params, legacy_site_params,
                 poll_interval, alerter=None, **_):
        self.http, self.cdn_http, self.db_pool = http, cdn_http, db_pool
        self.leagues = leagues
        self.alerter = alerter
        self.metrics = _FakeMetrics()
        self.run = AsyncMock()
        self.stop = lambda: None
        _FakeScheduler.instances.append(self)


def _patch_common(monkeypatch, calls, *, scheduler_cls=_FakeScheduler, migrate_result=None):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    monkeypatch.setattr(main_module, "load_settings", lambda: _settings())
    monkeypatch.setattr(main_module, "load_leagues", lambda: [SimpleNamespace(id=1, name="MKX", sport_id=103)])

    async def fake_migrate(dsn):
        calls.append(("migrate", dsn))
        return migrate_result or []

    async def fake_create_pool(dsn, **kwargs):
        calls.append(("create_pool", dsn))
        pool = AsyncMock()
        pool.close = AsyncMock(side_effect=lambda: calls.append(("pool_close",)))
        return pool

    monkeypatch.setattr(main_module, "migrate", fake_migrate)
    monkeypatch.setattr(main_module.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(main_module, "start_http_server", lambda *a, **k: calls.append(("start_http_server",)))
    monkeypatch.setattr(main_module, "Scheduler", scheduler_cls)

    class _FakeHttp:
        def __init__(self, *a, source_label="unknown", **k):
            self.source_label = source_label
            self.aclose = AsyncMock(side_effect=lambda: calls.append((f"aclose:{source_label}",)))

    monkeypatch.setattr(main_module, "HttpClient", _FakeHttp)

    # Pas de vrais gestionnaires de signal dans un test (pas de vraie boucle d'événements réutilisable).
    monkeypatch.setattr(main_module.asyncio.get_running_loop().__class__, "add_signal_handler",
                         lambda self, *a, **k: (_ for _ in ()).throw(NotImplementedError), raising=False)
    monkeypatch.setattr(main_module.signal, "signal", lambda *a, **k: None)

    monkeypatch.setattr(main_module, "load_alert_config_from_env",
                         lambda: SimpleNamespace(telegram_enabled=True, email_enabled=False))


async def test_amain_applies_migrations_before_creating_the_pool(monkeypatch):
    calls: list[tuple] = []
    _FakeScheduler.instances.clear()
    _patch_common(monkeypatch, calls, migrate_result=["005_allow_tied_results"])

    code = await main_module.amain()

    assert code == 0
    order = [c[0] for c in calls]
    assert order.index("migrate") < order.index("create_pool")


async def test_amain_returns_3_when_the_scheduler_was_blocked(monkeypatch):
    calls: list[tuple] = []
    _FakeScheduler.instances.clear()

    class _BlockedScheduler(_FakeScheduler):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.metrics.blocked = True

    _patch_common(monkeypatch, calls, scheduler_cls=_BlockedScheduler)

    code = await main_module.amain()

    assert code == 3


async def test_amain_closes_http_clients_and_pool_even_if_the_scheduler_raises(monkeypatch):
    calls: list[tuple] = []
    _FakeScheduler.instances.clear()

    class _RaisingScheduler(_FakeScheduler):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.run = AsyncMock(side_effect=RuntimeError("panne simulée"))

    _patch_common(monkeypatch, calls, scheduler_cls=_RaisingScheduler)

    with pytest.raises(RuntimeError, match="panne simulée"):
        await main_module.amain()

    assert ("pool_close",) in calls
    assert ("aclose:melbet",) in calls
    assert ("aclose:cdn",) in calls


async def test_amain_warns_when_no_alert_channel_is_configured(monkeypatch, caplog):
    calls: list[tuple] = []
    _FakeScheduler.instances.clear()
    _patch_common(monkeypatch, calls)
    monkeypatch.setattr(main_module, "load_alert_config_from_env",
                         lambda: SimpleNamespace(telegram_enabled=False, email_enabled=False))

    with caplog.at_level(logging.WARNING, logger="collector.main"):
        await main_module.amain()

    assert any("aucun canal d'alerte" in r.message for r in caplog.records)


def test_main_configures_logging_and_returns_asyncio_run_result(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(main_module, "configure_logging", lambda: calls.append("configure_logging"))
    monkeypatch.setattr(main_module.asyncio, "run", lambda coro: (coro.close(), calls.append("asyncio.run"), 0)[-1])

    result = main_module.main()

    assert result == 0
    assert calls == ["configure_logging", "asyncio.run"]
