"""Vérifie que les métriques Prometheus s'incrémentent et s'exportent correctement."""
from __future__ import annotations

from prometheus_client import generate_latest

from collector.observability import metrics


def test_counters_increment_and_export():
    metrics.requests_total.labels(source="melbet", endpoint="/x", outcome="success").inc()
    metrics.odds_rows_written_total.labels(league="1252965").inc(5)
    metrics.queue_depth.set(3)

    exported = generate_latest(metrics.REGISTRY).decode("utf-8")
    assert 'collector_requests_total{endpoint="/x",outcome="success",source="melbet"}' in exported
    assert "collector_odds_rows_written_total" in exported
    assert "collector_queue_depth 3.0" in exported


def test_last_cycle_timestamp_can_be_set_to_current_time():
    metrics.last_cycle_timestamp_seconds.labels(league="1252965").set_to_current_time()
    exported = generate_latest(metrics.REGISTRY).decode("utf-8")
    assert "collector_last_cycle_timestamp_seconds" in exported
