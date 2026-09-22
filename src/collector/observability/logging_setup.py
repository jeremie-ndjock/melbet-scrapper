"""Journaux structurés en JSON (voir docs/architecture.md, §14), avec masquage systématique des
secrets connus — filet de sécurité : le code ne journalise jamais lui-même un secret, mais une
future erreur ou un message d'exception mal formé ne doit jamais pouvoir en exposer un.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

_SECRET_ENV_VARS = ("EMAIL_HOST_PASSWORD", "TELEGRAM_BOT_TOKEN")


def _build_redactor() -> re.Pattern[str] | None:
    values = [os.environ.get(name, "") for name in _SECRET_ENV_VARS]
    values = [v for v in values if v]  # une variable absente ne doit jamais produire un motif vide
    if not values:
        return None
    return re.compile("|".join(re.escape(v) for v in values))


class RedactSecretsFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._pattern = _build_redactor()

    def filter(self, record: logging.LogRecord) -> bool:
        if self._pattern is not None:
            record.msg = self._pattern.sub("<masqué>", str(record.msg))
            if record.args:
                record.args = tuple(
                    self._pattern.sub("<masqué>", a) if isinstance(a, str) else a for a in record.args
                )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }
        for key in ("league_id", "game_id", "latency_ms", "status_code", "endpoint", "source"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactSecretsFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
