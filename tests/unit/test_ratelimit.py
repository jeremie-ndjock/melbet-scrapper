"""Tests directs du limiteur de débit et du coupe-circuit, sans passer par HttpClient (déjà
couverts indirectement dans test_http.py). Ici on isole la logique elle-même : espacement minimal
réel, et les trois états du coupe-circuit (fermé, ouvert, semi-ouvert) y compris la reprise."""
from __future__ import annotations

import asyncio
import time

import pytest

from collector.transport.ratelimit import CircuitBreaker, CircuitOpenError, RateLimiter


def test_rate_limiter_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(-1)


async def test_rate_limiter_enforces_minimum_interval_between_calls():
    limiter = RateLimiter(max_per_second=20)  # 50 ms minimum entre deux débuts d'appel
    start = time.monotonic()
    await limiter.wait()
    await limiter.wait()
    await limiter.wait()
    elapsed = time.monotonic() - start
    # Deux intervalles imposés d'au moins 50 ms chacun (marge pour la variabilité de l'horloge).
    assert elapsed >= 0.09


async def test_rate_limiter_does_not_delay_calls_already_spaced_out():
    limiter = RateLimiter(max_per_second=1000)  # 1 ms minimum : ne doit jamais ralentir un test
    start = time.monotonic()
    await asyncio.sleep(0.05)
    await limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # aucun délai supplémentaire ajouté par le limiteur


async def test_rate_limiter_serializes_concurrent_callers():
    """Plusieurs appelants concurrents ne doivent jamais démarrer en dessous de l'intervalle minimal,
    même s'ils appellent `wait()` exactement en même temps (le verrou interne les sérialise)."""
    limiter = RateLimiter(max_per_second=20)
    starts: list[float] = []

    async def call():
        await limiter.wait()
        starts.append(time.monotonic())

    await asyncio.gather(*(call() for _ in range(4)))
    starts.sort()
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 0.045  # marge sous les 50 ms théoriques


def test_circuit_breaker_starts_closed():
    breaker = CircuitBreaker(failure_threshold=3, recovery_seconds=60)
    assert not breaker.is_open
    breaker.before_call()  # ne lève rien


def test_circuit_breaker_opens_exactly_at_threshold_not_before():
    breaker = CircuitBreaker(failure_threshold=3, recovery_seconds=60)
    breaker.on_failure()
    breaker.on_failure()
    assert not breaker.is_open
    breaker.before_call()  # toujours fermé, ne lève rien
    breaker.on_failure()
    assert breaker.is_open
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_circuit_breaker_success_resets_failure_count():
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60)
    breaker.on_failure()
    breaker.on_success()  # remet le compteur à zéro avant d'atteindre le seuil
    breaker.on_failure()
    assert not breaker.is_open  # un seul échec depuis le reset : pas encore ouvert


def test_circuit_breaker_half_open_allows_one_call_after_recovery_delay():
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.05)
    breaker.on_failure()
    assert breaker.is_open
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    time.sleep(0.06)
    assert not breaker.is_open  # le délai de reprise est passé
    breaker.before_call()  # semi-ouvert : cet appel de test est autorisé


def test_circuit_breaker_half_open_success_closes_circuit_again():
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.05)
    breaker.on_failure()
    time.sleep(0.06)
    breaker.before_call()  # appel de test autorisé (semi-ouvert)
    breaker.on_success()
    assert not breaker.is_open
    breaker.before_call()  # de nouveau fermé normalement


def test_circuit_breaker_half_open_failure_reopens_for_another_full_delay():
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.05)
    breaker.on_failure()
    time.sleep(0.06)
    breaker.before_call()  # appel de test autorisé (semi-ouvert)
    breaker.on_failure()  # échoue de nouveau : réouvre le circuit
    assert breaker.is_open
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
