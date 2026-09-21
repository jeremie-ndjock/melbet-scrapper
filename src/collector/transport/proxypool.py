"""Bassin de proxies : rotation, contrôle de santé, retrait temporaire, restauration automatique.

Réalisé mais **désactivé par défaut** (décision D5, voir docs/architecture.md) : la reconnaissance
n'a trouvé aucune protection anti-bot ni blocage géographique sur ce site (Memoire.md, section 5).
Ce module reste prêt à l'emploi si l'IP du VPS venait un jour à être bloquée (Memoire.md, section 12,
risque 6) — un scénario à tester, pas à supposer.

Ce module ne fait *pas* de rotation d'identité pour échapper à une détection (voir Memoire.md,
section 20) : son seul rôle est de retrouver une adresse IP acceptée si celle du VPS ne l'est pas,
ce qui est un problème de routage réseau, pas un contournement d'une protection active du site.

Le câblage dans ``HttpClient`` (proxy réellement utilisé pour chaque requête) n'est fait que si un
besoin réel apparaît : httpx lie un proxy au client à sa construction, pas requête par requête, ce
qui demande un client par proxy actif plutôt qu'un simple paramètre.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Proxy:
    endpoint: str
    healthy: bool = True
    consecutive_failures: int = 0
    requests_count: int = 0
    last_failure: float | None = None
    cooldown_until: float = 0.0


class ProxyPool:
    def __init__(self, endpoints: list[str] | None = None, *, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        self._proxies: list[Proxy] = [Proxy(e) for e in (endpoints or [])]
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._next_index = 0

    @property
    def enabled(self) -> bool:
        return bool(self._proxies)

    def _restore_expired(self) -> None:
        now = time.monotonic()
        for p in self._proxies:
            if not p.healthy and now >= p.cooldown_until:
                p.healthy = True
                p.consecutive_failures = 0

    def acquire(self) -> str | None:
        """Retourne l'adresse du prochain proxy sain (rotation simple), ou ``None`` si aucun ne
        l'est — l'appelant doit alors se rabattre sur une connexion directe, jamais boucler dessus."""
        self._restore_expired()
        available = [p for p in self._proxies if p.healthy]
        if not available:
            return None
        proxy = available[self._next_index % len(available)]
        self._next_index += 1
        proxy.requests_count += 1
        return proxy.endpoint

    def report_success(self, endpoint: str) -> None:
        for p in self._proxies:
            if p.endpoint == endpoint:
                p.consecutive_failures = 0

    def report_failure(self, endpoint: str) -> None:
        for p in self._proxies:
            if p.endpoint == endpoint:
                p.consecutive_failures += 1
                p.last_failure = time.monotonic()
                if p.consecutive_failures >= self._failure_threshold:
                    p.healthy = False
                    p.cooldown_until = time.monotonic() + self._cooldown_seconds

    def stats(self) -> list[Proxy]:
        self._restore_expired()
        return list(self._proxies)
