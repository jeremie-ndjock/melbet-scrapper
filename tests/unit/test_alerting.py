"""Tests des alertes : configuration, envoi (Telegram et e-mail simulés), limitation par clé."""
from __future__ import annotations

import smtplib

import httpx
import pytest

from collector.alerting import AlertConfig, AlertSender, ThrottledAlerter, load_alert_config_from_env

TELEGRAM_CONFIG = AlertConfig(telegram_bot_token="123:abc", telegram_chat_id="42")
EMAIL_CONFIG = AlertConfig(email_host="smtp.test", email_from="from@test", email_to="to@test")
BOTH_CONFIG = AlertConfig(
    telegram_bot_token="123:abc", telegram_chat_id="42",
    email_host="smtp.test", email_from="from@test", email_to="to@test",
)
NONE_CONFIG = AlertConfig()


def test_config_reports_which_channels_are_enabled():
    assert TELEGRAM_CONFIG.telegram_enabled and not TELEGRAM_CONFIG.email_enabled
    assert EMAIL_CONFIG.email_enabled and not EMAIL_CONFIG.telegram_enabled
    assert not NONE_CONFIG.telegram_enabled and not NONE_CONFIG.email_enabled


def test_load_alert_config_from_env_reads_expected_variables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("EMAIL_HOST", "smtp.test")
    monkeypatch.setenv("EMAIL_FROM", "a@b")
    monkeypatch.setenv("EMAIL_TO", "c@d")
    monkeypatch.delenv("EMAIL_HOST_PASSWORD", raising=False)
    config = load_alert_config_from_env()
    assert config.telegram_enabled and config.email_enabled
    assert config.email_host_password is None  # variable absente : None, pas une chaîne vide


async def test_send_with_no_channel_configured_does_not_raise(caplog):
    sender = AlertSender(NONE_CONFIG)
    await sender.send("sujet", "message")  # ne doit lever aucune exception


async def test_send_telegram_posts_expected_message(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, data):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    sender = AlertSender(TELEGRAM_CONFIG)
    await sender.send("Sujet", "Corps du message")
    assert captured["data"]["chat_id"] == "42"
    assert "Sujet" in captured["data"]["text"] and "Corps du message" in captured["data"]["text"]
    assert "123:abc" in captured["url"]  # le jeton fait partie de l'URL de l'API Telegram elle-même


async def test_send_email_uses_smtp_with_tls_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["Subject"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    config = AlertConfig(email_host="smtp.test", email_port=587, email_host_user="u", email_host_password="p",
                          email_use_tls=True, email_from="from@test", email_to="to@test")
    sender = AlertSender(config)
    await sender.send("Sujet", "Corps")
    kinds = [c[0] for c in calls]
    assert kinds == ["connect", "ehlo", "starttls", "ehlo", "login", "send"]


async def test_send_failure_on_one_channel_does_not_prevent_the_other(monkeypatch, caplog):
    async def broken_telegram(self, subject, message):
        raise RuntimeError("panne simulée")

    sent_email = {}
    monkeypatch.setattr(AlertSender, "_send_telegram", broken_telegram)
    monkeypatch.setattr(AlertSender, "_send_email", lambda self, s, m: sent_email.update(subject=s))
    sender = AlertSender(BOTH_CONFIG)
    await sender.send("Sujet", "Corps")  # ne lève pas, malgré l'échec Telegram
    assert sent_email["subject"] == "Sujet"


class FakeSender:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def send(self, subject: str, message: str) -> None:
        self.calls.append((subject, message))


async def test_throttled_alerter_suppresses_repeats_within_cooldown():
    fake = FakeSender()
    alerter = ThrottledAlerter(fake, cooldown_seconds=999)
    assert await alerter.alert("k", "s1", "m1") is True
    assert await alerter.alert("k", "s2", "m2") is False  # supprimée : même clé, dans le délai
    assert len(fake.calls) == 1


async def test_throttled_alerter_allows_different_keys_immediately():
    fake = FakeSender()
    alerter = ThrottledAlerter(fake, cooldown_seconds=999)
    assert await alerter.alert("a", "s", "m") is True
    assert await alerter.alert("b", "s", "m") is True
    assert len(fake.calls) == 2


async def test_throttled_alerter_allows_repeat_after_cooldown_expires():
    fake = FakeSender()
    alerter = ThrottledAlerter(fake, cooldown_seconds=0.01)
    assert await alerter.alert("k", "s", "m") is True
    import asyncio
    await asyncio.sleep(0.02)
    assert await alerter.alert("k", "s", "m") is True
    assert len(fake.calls) == 2


async def test_throttled_alerter_reset_clears_a_key_immediately():
    fake = FakeSender()
    alerter = ThrottledAlerter(fake, cooldown_seconds=999)
    await alerter.alert("k", "s", "m")
    alerter.reset("k")
    assert await alerter.alert("k", "s", "m") is True  # pas d'attente nécessaire après reset
    assert len(fake.calls) == 2
