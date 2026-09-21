"""Erreurs de transport.

Distinction volontaire entre deux familles :
- les erreurs *transitoires* (``ServerError``, coupures réseau, délais dépassés) sont réessayées
  automatiquement avec une attente exponentielle et un facteur aléatoire ;
- ``BlockedError`` (403, 429, ou tout signal de blocage) n'est **jamais** réessayée automatiquement
  ni contournée : elle remonte à l'appelant, qui doit s'arrêter et alerter. Le collecteur ne change
  jamais d'identité (User-Agent, en-têtes, IP) pour continuer après un blocage.
"""
from __future__ import annotations


class TransportError(Exception):
    """Erreur de transport, quelle qu'elle soit."""


class ServerError(TransportError):
    """Erreur transitoire (5xx, timeout, coupure réseau). Peut être réessayée."""


class BlockedError(TransportError):
    """Le site a explicitement refusé la requête (403, 429). Ne jamais réessayer automatiquement
    ni changer d'identité : l'appelant doit arrêter la collecte et alerter."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}" + (f": {message}" if message else ""))


class ParserError(Exception):
    """La réponse ne correspond pas au schéma attendu (structure changée, JSON invalide)."""

    def __init__(self, message: str, *, endpoint: str, payload: str):
        self.endpoint = endpoint
        self.payload = payload
        super().__init__(message)
