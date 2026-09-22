"""Tests des journaux structurés : format JSON, masquage des secrets."""
from __future__ import annotations

import json
import logging

from collector.observability.logging_setup import JsonFormatter, RedactSecretsFilter


def make_record(msg, *, args=(), level=logging.INFO):
    return logging.LogRecord("collector.test", level, __file__, 1, msg, args, None)


def test_json_formatter_produces_valid_json_with_expected_fields():
    formatter = JsonFormatter()
    record = make_record("un message")
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["component"] == "collector.test"
    assert payload["message"] == "un message"
    assert "timestamp" in payload


def test_json_formatter_includes_extra_context_fields():
    formatter = JsonFormatter()
    record = make_record("relevé")
    record.league_id = 1252965
    record.latency_ms = 700
    record.status_code = 200
    payload = json.loads(formatter.format(record))
    assert payload["league_id"] == 1252965 and payload["latency_ms"] == 700 and payload["status_code"] == 200


def test_json_formatter_includes_exception_info():
    formatter = JsonFormatter()
    try:
        raise ValueError("erreur de test")
    except ValueError:
        import sys
        record = make_record("échec")
        record.exc_info = sys.exc_info()
    payload = json.loads(formatter.format(record))
    assert "ValueError" in payload["exception"]


def test_redact_filter_masks_configured_secrets(monkeypatch):
    monkeypatch.setenv("EMAIL_HOST_PASSWORD", "motdepasse-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:jeton-secret")
    f = RedactSecretsFilter()
    record = make_record("échec avec motdepasse-secret et 123456:jeton-secret dans le message")
    f.filter(record)
    assert "motdepasse-secret" not in record.msg
    assert "jeton-secret" not in record.msg
    assert "<masqué>" in record.msg


def test_redact_filter_masks_secrets_in_args_too(monkeypatch):
    monkeypatch.setenv("EMAIL_HOST_PASSWORD", "secret-arg")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    f = RedactSecretsFilter()
    record = make_record("valeur : %s", args=("secret-arg",))
    f.filter(record)
    assert record.args[0] == "<masqué>"


def test_redact_filter_is_a_no_op_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("EMAIL_HOST_PASSWORD", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    f = RedactSecretsFilter()
    record = make_record("message ordinaire")
    assert f.filter(record) is True
    assert record.msg == "message ordinaire"
