"""Client HTTP honnête : une seule identité fixe, aucune évasion.

Règles suivies (voir Memoire.md, section « anti-scraping ») :
- User-Agent fixe et descriptif, jamais de rotation ni d'imitation de navigateur ;
- aucun en-tête ou jeton de session copié depuis un navigateur ;
- aucune imitation d'empreinte TLS ;
- reprise automatique uniquement sur les erreurs transitoires (timeout, coupure, 5xx) ;
- un blocage explicite (403, 429) n'est jamais réessayé automatiquement : il lève ``BlockedError``,
  à charge de l'appelant de s'arrêter et d'alerter, sans changer d'identité pour continuer.

Contrainte du site (mesurée) : les paramètres de requête doivent être triés par ordre alphabétique
de leur nom, sinon le serveur répond 400.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import httpx

from ..observability import metrics
from .errors import BlockedError, ServerError
from .ratelimit import CircuitBreaker, CircuitOpenError, RateLimiter

log = logging.getLogger("collector.transport")


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    body: str
    latency_ms: int


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0


class HttpClient:
    """Enveloppe httpx.AsyncClient avec limiteur de débit, coupe-circuit et reprises bornées."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        *,
        timeout_seconds: float = 10.0,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        retry: RetryConfig | None = None,
        client: httpx.AsyncClient | None = None,
        source_label: str = "unknown",
    ):
        self.source_label = source_label
        self._retry = retry or RetryConfig()
        self._rate_limiter = rate_limiter or RateLimiter(1.0)
        self._circuit = circuit_breaker or CircuitBreaker(failure_threshold=5, recovery_seconds=30)
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    @staticmethod
    def _sorted_query(params: dict[str, object]) -> str:
        """Sérialise les paramètres triés par ordre alphabétique du nom (exigence du site)."""
        return "&".join(f"{k}={params[k]}" for k in sorted(params))

    async def get(self, path: str, params: dict[str, object] | None = None) -> FetchResult:
        """GET avec reprise bornée sur erreur transitoire. Lève ``BlockedError`` sans réessayer
        sur 403/429 ; lève ``ServerError`` si toutes les tentatives transitoires ont échoué."""
        url = f"{path}?{self._sorted_query(params)}" if params else path
        last_error: Exception | None = None
        src = self.source_label

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                self._circuit.before_call()  # lève CircuitOpenError si ouvert
            except CircuitOpenError:
                metrics.circuit_open_total.labels(source=src).inc()
                raise
            await self._rate_limiter.wait()
            t0 = time.perf_counter()
            try:
                response = await self._client.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self._circuit.on_failure()
                last_error = exc
                metrics.requests_total.labels(source=src, endpoint=path, outcome="timeout").inc()
                await self._backoff(attempt)
                continue

            latency_ms = round((time.perf_counter() - t0) * 1000)

            if response.status_code in (403, 429):
                self._circuit.on_failure()
                metrics.requests_total.labels(source=src, endpoint=path, outcome="blocked").inc()
                metrics.blocked_total.labels(source=src).inc()
                raise BlockedError(response.status_code, response.text[:200])

            if response.status_code >= 500:
                self._circuit.on_failure()
                last_error = ServerError(f"HTTP {response.status_code}")
                metrics.requests_total.labels(source=src, endpoint=path, outcome="server_error").inc()
                await self._backoff(attempt)
                continue

            self._circuit.on_success()
            metrics.requests_total.labels(source=src, endpoint=path, outcome="success").inc()
            metrics.request_latency_seconds.labels(source=src, endpoint=path).observe(latency_ms / 1000)
            return FetchResult(response.status_code, response.text, latency_ms)

        assert last_error is not None
        raise ServerError(f"échec après {self._retry.max_attempts} tentatives : {last_error}") from last_error

    async def _backoff(self, attempt: int) -> None:
        metrics.retries_total.labels(source=self.source_label).inc()
        delay = min(self._retry.base_delay_seconds * (2 ** (attempt - 1)), self._retry.max_delay_seconds)
        delay *= 0.5 + random.random()  # jitter : évite que des cycles synchronisés ne réessaient ensemble
        log.warning("tentative %d/%d échouée, nouvelle tentative dans %.1fs", attempt, self._retry.max_attempts, delay)
        await asyncio.sleep(delay)


__all__ = ["HttpClient", "RetryConfig", "FetchResult", "CircuitOpenError"]
