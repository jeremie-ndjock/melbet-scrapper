"""Surveillance externe du collecteur (Memoire.md, section 9 : « watchdog externe (cron), pas
d'Alertmanager », décision D4).

Volontairement indépendant du reste du code applicatif (n'utilise que ``asyncpg`` et
``collector.alerting``) : il doit continuer à fonctionner même si le collecteur lui-même est en
panne plus profonde que ce qu'il peut détecter sur lui-même. Une base injoignable est elle-même un
signal d'alerte : elle couvre le cas où tout le conteneur (ou le VPS) serait à l'arrêt, pas
seulement le sondage d'une ligue.

À lancer périodiquement en dehors de Docker (cron de l'hôte), par exemple toutes les 5 min :

    */5 * * * * cd /chemin/du/projet && DATABASE_URL=... python scripts/watchdog.py

Variables d'environnement :
- ``DATABASE_URL`` (obligatoire)
- ``WATCHDOG_LEAGUE_IDS`` : identifiants de ligue séparés par des virgules (défaut : les deux
  ligues validées, Memoire.md section 1)
- ``WATCHDOG_MAX_AGE_SECONDS`` : ancienneté maximale tolérée du dernier cycle réussi (défaut 120 s,
  soit environ 24 cycles de 5 s : largement au-dessus du bruit normal, sans être trop permissif)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncpg  # noqa: E402

from collector.alerting import AlertSender, ThrottledAlerter, load_alert_config_from_env  # noqa: E402

DEFAULT_LEAGUE_IDS = "1252965,2282406"
MAX_AGE_SECONDS = int(os.environ.get("WATCHDOG_MAX_AGE_SECONDS", "120"))
LEAGUE_IDS = [int(x) for x in os.environ.get("WATCHDOG_LEAGUE_IDS", DEFAULT_LEAGUE_IDS).split(",")]


async def check_league(conn: asyncpg.Connection, league_id: int) -> float | None:
    """Ancienneté (en secondes) du dernier cycle réussi, ou ``None`` si aucun n'a jamais eu lieu."""
    last_ok = await conn.fetchval(
        "SELECT max(ts) FROM collection_log WHERE league_id = $1 AND ok", league_id,
    )
    if last_ok is None:
        return None
    return (datetime.now(timezone.utc) - last_ok).total_seconds()


async def run_check(conn: asyncpg.Connection, alerter: ThrottledAlerter, league_ids: list[int],
                     max_age_seconds: int) -> bool:
    """Logique testable, indépendante de la construction des vraies ressources (base, alerteur).
    Retourne ``True`` si tout va bien, ``False`` si une alerte de retard a été déclenchée."""
    stale = []
    for league_id in league_ids:
        age = await check_league(conn, league_id)
        if age is None or age > max_age_seconds:
            stale.append((league_id, age))

    if not stale:
        return True

    lines = [
        f"- ligue {league_id} : "
        + ("aucun cycle jamais enregistré" if age is None else f"dernier cycle réussi il y a {age:.0f} s")
        for league_id, age in stale
    ]
    await alerter.alert(
        "watchdog:stale", "⚠️ Watchdog : collecte arrêtée ou en retard",
        f"Aucun cycle réussi récent (seuil : {max_age_seconds} s) :\n" + "\n".join(lines),
    )
    return False


async def main() -> int:
    alerter = ThrottledAlerter(AlertSender(load_alert_config_from_env()))

    try:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)
    except Exception as exc:
        await alerter.alert(
            "watchdog:db_unreachable", "🛑 Watchdog : base de données injoignable",
            f"Impossible de se connecter à la base : {type(exc).__name__}: {exc}\n"
            "Le collecteur est probablement à l'arrêt (conteneur, VPS, ou réseau) : "
            "vérification humaine nécessaire.",
        )
        return 1

    try:
        healthy = await run_check(conn, alerter, LEAGUE_IDS, MAX_AGE_SECONDS)
        return 0 if healthy else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
