"""Tests du client HTTP : reprises sur erreur transitoire, aucune reprise sur blocage,
coupe-circuit, tri alphabétique des paramètres. Aucun réseau réel (httpx.MockTransport)."""
from __future__ import annotations

import httpx
import pytest

from collector.transport.errors import BlockedError, ServerError
from collector.transport.http import HttpClient, RetryConfig
from collector.transport.ratelimit import CircuitBreaker, CircuitOpenError, RateLimiter


def make_client(handler, **kwargs) -> HttpClient:
    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(transport=transport, base_url="https://example.test",
                               headers={"User-Agent": "OddsCollector/1.0"})
    return HttpClient("https://example.test", "OddsCollector/1.0",
                       rate_limiter=RateLimiter(1000), client=inner, **kwargs)


async def test_user_agent_is_fixed_and_never_rotated():
    """L'identification est toujours la même valeur configurée, jamais une autre."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, text="{}")

    client = make_client(handler)
    for _ in range(3):
        await client.get("/x", {"a": 1})
    assert seen == ["OddsCollector/1.0"] * 3  # jamais de rotation


async def test_query_params_are_sorted_alphabetically():
    seen_query = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_query["q"] = str(request.url.query, "ascii")
        return httpx.Response(200, text="{}")

    client = make_client(handler)
    await client.get("/x", {"zeta": 1, "alpha": 2, "middle": 3})
    assert seen_query["q"] == "alpha=2&middle=3&zeta=1"


async def test_transient_server_error_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, text='{"ok": true}')

    client = make_client(handler, retry=RetryConfig(max_attempts=5, base_delay_seconds=0.001, max_delay_seconds=0.01))
    result = await client.get("/x", {})
    assert result.status_code == 200 and calls["n"] == 3


async def test_persistent_server_error_raises_after_max_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = make_client(handler, retry=RetryConfig(max_attempts=2, base_delay_seconds=0.001, max_delay_seconds=0.01))
    with pytest.raises(ServerError):
        await client.get("/x", {})


async def test_timeout_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.TimeoutException("délai dépassé (simulé)")
        return httpx.Response(200, text='{"ok": true}')

    client = make_client(handler, retry=RetryConfig(max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.01))
    result = await client.get("/x", {})
    assert result.status_code == 200 and calls["n"] == 2


async def test_persistent_timeout_raises_server_error_not_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("délai dépassé (simulé)")

    client = make_client(handler, retry=RetryConfig(max_attempts=2, base_delay_seconds=0.001, max_delay_seconds=0.01))
    with pytest.raises(ServerError):  # transitoire : jamais confondu avec un blocage
        await client.get("/x", {})


async def test_403_raises_blocked_error_immediately_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    client = make_client(handler, retry=RetryConfig(max_attempts=5, base_delay_seconds=0.001, max_delay_seconds=0.01))
    with pytest.raises(BlockedError) as exc_info:
        await client.get("/x", {})
    assert calls["n"] == 1  # aucune reprise, aucun changement d'identité, une seule tentative
    assert exc_info.value.status_code == 403


async def test_429_raises_blocked_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = make_client(handler)
    with pytest.raises(BlockedError):
        await client.get("/x", {})


async def test_circuit_breaker_opens_after_repeated_failures_and_blocks_further_calls():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60)
    client = make_client(handler, circuit_breaker=breaker,
                          retry=RetryConfig(max_attempts=1, base_delay_seconds=0.001, max_delay_seconds=0.01))

    with pytest.raises(ServerError):
        await client.get("/x", {})
    assert not breaker.is_open  # un seul échec, sous le seuil de 2

    with pytest.raises(ServerError):
        await client.get("/x", {})
    assert breaker.is_open  # deuxième échec : seuil atteint, circuit ouvert

    with pytest.raises(CircuitOpenError):
        await client.get("/x", {})
    assert calls["n"] == 2  # le troisième appel n'a même pas atteint le réseau
