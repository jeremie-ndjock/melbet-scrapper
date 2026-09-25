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
    assert record.getMessage() == "valeur : <masqué>"


class _Url:
    """Comme ``httpx.URL`` : un objet, pas une chaîne, dont le texte contient le jeton."""

    def __str__(self):
        return "https://api.telegram.org/bot123456:jeton-secret/sendMessage"


def test_redact_filter_masks_secrets_hidden_in_non_string_args(monkeypatch):
    """Trouvé en production le 2026-09-25 : httpx journalise l'URL de l'API Telegram (qui contient
    le jeton du bot) sous forme d'objet, que l'ancien filtre ne masquait pas."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:jeton-secret")
    f = RedactSecretsFilter()
    record = make_record('HTTP Request: %s %s "%s"', args=("POST", _Url(), "HTTP/1.1 200 OK"))
    f.filter(record)
    assert "jeton-secret" not in record.getMessage()
    assert "bot<masqué>/sendMessage" in record.getMessage()


def test_redact_filter_keeps_a_single_mapping_argument_readable(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:jeton-secret")
    record = make_record("historique : %s", args=({"a": 1},))
    RedactSecretsFilter().filter(record)
    assert record.getMessage() == "historique : {'a': 1}"


def test_redact_filter_masks_secrets_in_exception_text(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:jeton-secret")
    try:
        raise RuntimeError("échec vers https://api.telegram.org/bot123456:jeton-secret/sendMessage")
    except RuntimeError:
        import sys
        record = logging.LogRecord("collector.test", logging.ERROR, __file__, 1, "erreur", (), sys.exc_info())
    RedactSecretsFilter().filter(record)
    out = JsonFormatter().format(record)
    assert "jeton-secret" not in out and "<masqué>" in out


def test_redact_filter_is_a_no_op_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("EMAIL_HOST_PASSWORD", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    f = RedactSecretsFilter()
    record = make_record("message ordinaire")
    assert f.filter(record) is True
    assert record.msg == "message ordinaire"
