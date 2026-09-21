"""Limiteur de débit simple (espacement minimal entre requêtes) et coupe-circuit.

Aucun des deux ne sert à contourner une protection : le limiteur protège le site cible d'une charge
excessive, le coupe-circuit protège le collecteur lui-même en cessant d'insister quand le site est
manifestement en panne (erreurs 5xx répétées).
"""
from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Impose un espacement minimal entre le début de deux requêtes."""

    def __init__(self, max_per_second: float):
        if max_per_second <= 0:
            raise ValueError("max_per_second doit être positif")
        self._min_interval = 1.0 / max_per_second
        self._lock = asyncio.Lock()
        self._last_start = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._last_start + self._min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_start = time.monotonic()


class CircuitOpenError(Exception):
    """Le coupe-circuit est ouvert : on n'essaie même pas d'appeler le site."""


class CircuitBreaker:
    """Coupe-circuit à trois états (fermé, ouvert, semi-ouvert), sans dépendance externe.

    - fermé (normal) : les appels passent ; ``failure_threshold`` échecs consécutifs l'ouvrent.
    - ouvert : les appels sont refusés immédiatement pendant ``recovery_seconds``.
    - semi-ouvert : after ce délai, un seul appel est autorisé pour tester la reprise.
    """

    def __init__(self, failure_threshold: int, recovery_seconds: float):
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def before_call(self) -> None:
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at < self._recovery:
            raise CircuitOpenError(f"coupe-circuit ouvert depuis {time.monotonic() - self._opened_at:.0f}s")
        # semi-ouvert : on laisse passer cet appel pour tester la reprise.

    def on_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def on_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and time.monotonic() - self._opened_at < self._recovery
