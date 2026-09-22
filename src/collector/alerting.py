"""Alertes Telegram et e-mail (Memoire.md, section 19 pour la configuration, section 11 pour la
politique de limitation).

``AlertSender`` envoie sur les deux canaux configurés, sans jamais lever : un échec d'envoi
d'alerte est journalisé, pas propagé (une alerte qui interrompt la collecte serait pire que son
absence). ``ThrottledAlerter`` regroupe les alertes identiques : au plus une par ``cooldown_seconds``
pour une même clé, afin de ne pas harceler l'utilisateur (et de rester dans le quota du domaine de
démonstration Mailtrap, Memoire.md section 12) en cas de panne prolongée.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

log = logging.getLogger("collector.alerting")

DEFAULT_COOLDOWN_SECONDS = 1800.0  # 30 min (Memoire.md, section 11)


@dataclass(frozen=True)
class AlertConfig:
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    email_host: str | None = None
    email_port: int = 587
    email_host_user: str | None = None
    email_host_password: str | None = None
    email_use_tls: bool = True
    email_from: str | None = None
    email_to: str | None = None

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_host and self.email_from and self.email_to)


def load_alert_config_from_env() -> AlertConfig:
    """Lit la configuration depuis les variables d'environnement (``.env``). Un canal dont les
    variables sont absentes est simplement désactivé, pas une erreur : l'utilisateur peut choisir
    de n'en configurer qu'un seul."""
    return AlertConfig(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        email_host=os.environ.get("EMAIL_HOST") or None,
        email_port=int(os.environ.get("EMAIL_PORT", "587")),
        email_host_user=os.environ.get("EMAIL_HOST_USER") or None,
        email_host_password=os.environ.get("EMAIL_HOST_PASSWORD") or None,
        email_use_tls=os.environ.get("EMAIL_USE_TLS", "True").lower() == "true",
        email_from=os.environ.get("EMAIL_FROM") or None,
        email_to=os.environ.get("EMAIL_TO") or None,
    )


class AlertSender:
    def __init__(self, config: AlertConfig):
        self._config = config

    async def send(self, subject: str, message: str) -> None:
        """Envoie sur tous les canaux configurés, en parallèle. N'importe quel échec est
        journalisé (avec les secrets déjà masqués par le filtre de journalisation) et n'empêche
        pas l'envoi sur l'autre canal ni ne remonte à l'appelant."""
        tasks = []
        if self._config.telegram_enabled:
            tasks.append(self._send_telegram(subject, message))
        if self._config.email_enabled:
            tasks.append(asyncio.to_thread(self._send_email, subject, message))
        if not tasks:
            log.warning("aucun canal d'alerte configuré : %s — %s", subject, message)
            return
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                log.error("échec d'envoi d'une alerte : %s: %s", type(result).__name__, result)

    async def _send_telegram(self, subject: str, message: str) -> None:
        text = f"{subject}\n\n{message}" if subject else message
        url = f"https://api.telegram.org/bot{self._config.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, data={"chat_id": self._config.telegram_chat_id, "text": text})
            response.raise_for_status()

    def _send_email(self, subject: str, message: str) -> None:
        c = self._config
        msg = EmailMessage()
        msg["From"] = c.email_from
        msg["To"] = c.email_to
        msg["Subject"] = subject
        msg.set_content(message)
        with smtplib.SMTP(c.email_host, c.email_port, timeout=15) as smtp:
            smtp.ehlo()
            if c.email_use_tls:
                smtp.starttls()
                smtp.ehlo()
            if c.email_host_user and c.email_host_password:
                smtp.login(c.email_host_user, c.email_host_password)
            smtp.send_message(msg)


class ThrottledAlerter:
    """Enveloppe un ``AlertSender`` (ou tout objet compatible, pour les tests) avec une limitation
    par clé : une alerte identique n'est renvoyée qu'après ``cooldown_seconds``."""

    def __init__(self, sender, *, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self._sender = sender
        self._cooldown = cooldown_seconds
        self._last_sent: dict[str, float] = {}

    async def alert(self, key: str, subject: str, message: str) -> bool:
        """Envoie l'alerte, sauf si la même clé a déjà été envoyée il y a moins de
        ``cooldown_seconds``. Retourne ``True`` si l'alerte a effectivement été envoyée."""
        now = time.monotonic()
        last = self._last_sent.get(key)
        if last is not None and now - last < self._cooldown:
            return False
        self._last_sent[key] = now
        await self._sender.send(subject, message)
        return True

    def reset(self, key: str) -> None:
        """Efface la limitation d'une clé (ex. après un rétablissement), pour qu'une nouvelle
        occurrence du même problème soit signalée sans attendre la fin du délai."""
        self._last_sent.pop(key, None)
