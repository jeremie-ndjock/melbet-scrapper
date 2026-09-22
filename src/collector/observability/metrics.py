"""Métriques Prometheus (voir docs/architecture.md, §14, liste minimale).

Registre dédié (``REGISTRY``), pas le registre global par défaut de ``prometheus_client`` :
enregistrer deux fois le même nom de métrique (ex. en rechargeant le module dans un test) ne lève
pas d'erreur surprenante, et le futur point de montage ``/metrics`` reste explicite sur ce qu'il sert.

Ce qui n'est **pas** mesuré, et pourquoi (mieux vaut l'absence d'un chiffre que d'en inventer un) :
- ``authentication_failures`` : sans objet, ce site ne demande aucune authentification
  (Memoire.md, section 4).
- ``duplicates`` : ``asyncpg`` ne rapporte pas le nombre de lignes réellement ignorées par
  ``ON CONFLICT DO NOTHING`` sur un ``executemany`` ; l'option A (Memoire.md, section 16) fait de
  toute façon qu'un doublon véritable ne devrait jamais se produire hors reprise après panne.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()

requests_total = Counter(
    "collector_requests_total", "Requêtes sortantes, par issue.",
    ["source", "endpoint", "outcome"], registry=REGISTRY,
)
request_latency_seconds = Histogram(
    "collector_request_latency_seconds", "Latence des requêtes sorties avec succès.",
    ["source", "endpoint"], registry=REGISTRY,
)
retries_total = Counter(
    "collector_retries_total", "Tentatives de reprise après une erreur transitoire.",
    ["source"], registry=REGISTRY,
)
circuit_open_total = Counter(
    "collector_circuit_open_total", "Appels refusés parce que le coupe-circuit était ouvert.",
    ["source"], registry=REGISTRY,
)
blocked_total = Counter(
    "collector_blocked_total", "Blocages détectés (403/429) : l'ordonnanceur s'arrête à chacun.",
    ["source"], registry=REGISTRY,
)
parser_errors_total = Counter(
    "collector_parser_errors_total", "Réponses qui ne valident plus le schéma attendu.",
    ["source", "league"], registry=REGISTRY,
)
schema_changes_total = Counter(
    "collector_schema_changes_total", "Bascules vers la source de secours.",
    ["league"], registry=REGISTRY,
)
events_collected_total = Counter(
    "collector_events_collected_total", "Matchs vus dans un relevé.",
    ["league"], registry=REGISTRY,
)
odds_rows_written_total = Counter(
    "collector_odds_rows_written_total", "Lignes de cotes écrites (option A : sur changement réel).",
    ["league"], registry=REGISTRY,
)
queue_depth = Gauge(
    "collector_queue_depth", "Éléments en attente d'écriture dans la file bornée.", registry=REGISTRY,
)
database_write_seconds = Histogram(
    "collector_database_write_seconds", "Durée d'un cycle d'écriture en base (un relevé).", registry=REGISTRY,
)
proxy_pool_healthy = Gauge(
    "collector_proxy_pool_healthy", "Proxies sains dans le bassin (0 si désactivé, cas normal).",
    registry=REGISTRY,
)
last_cycle_timestamp_seconds = Gauge(
    "collector_last_cycle_timestamp_seconds", "Horodatage (epoch) du dernier cycle écrit avec succès.",
    ["league"], registry=REGISTRY,
)
