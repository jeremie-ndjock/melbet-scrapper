"""Tests du bassin de proxies (réalisé mais désactivé par défaut, voir Memoire.md section 20)."""
from __future__ import annotations

from collector.transport.proxypool import ProxyPool


def test_disabled_when_no_endpoint_configured():
    pool = ProxyPool([])
    assert pool.enabled is False
    assert pool.acquire() is None


def test_enabled_pool_rotates_across_endpoints():
    pool = ProxyPool(["http://p1", "http://p2"])
    assert pool.enabled is True
    seen = {pool.acquire() for _ in range(4)}
    assert seen == {"http://p1", "http://p2"}


def test_proxy_is_retired_after_repeated_failures():
    pool = ProxyPool(["http://p1", "http://p2"], failure_threshold=2, cooldown_seconds=999)
    pool.report_failure("http://p1")
    assert {p.endpoint for p in pool.stats() if p.healthy} == {"http://p1", "http://p2"}  # 1 échec : pas encore retiré
    pool.report_failure("http://p1")
    assert {p.endpoint for p in pool.stats() if p.healthy} == {"http://p2"}  # 2 échecs (le seuil) : retiré

    # Le proxy retiré n'est jamais renvoyé tant qu'il n'est pas restauré.
    for _ in range(6):
        assert pool.acquire() == "http://p2"


def test_success_resets_failure_counter():
    pool = ProxyPool(["http://p1"], failure_threshold=2)
    pool.report_failure("http://p1")
    pool.report_success("http://p1")
    pool.report_failure("http://p1")  # un seul échec net : sous le seuil
    assert pool.stats()[0].healthy is True


def test_retired_proxy_is_restored_after_cooldown():
    pool = ProxyPool(["http://p1"], failure_threshold=1, cooldown_seconds=0.0)  # restauration immédiate
    pool.report_failure("http://p1")
    assert pool.acquire() == "http://p1"  # le délai est déjà écoulé (0 s)


def test_no_healthy_proxy_returns_none_without_looping():
    pool = ProxyPool(["http://p1"], failure_threshold=1, cooldown_seconds=999)
    pool.report_failure("http://p1")
    assert pool.acquire() is None  # jamais de boucle à la recherche d'un proxy inexistant


def test_requests_count_is_tracked_per_proxy():
    pool = ProxyPool(["http://p1"])
    pool.acquire()
    pool.acquire()
    assert pool.stats()[0].requests_count == 2
