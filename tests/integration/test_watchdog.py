"""Tests du script de surveillance externe (base réelle, alerteur simulé)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from watchdog import check_league, run_check

MK_X, MK_3 = 1252965, 2282406


class FakeAlerter:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    async def alert(self, key: str, subject: str, message: str) -> bool:
        self.calls.append((key, subject, message))
        return True


async def log_cycle(db, *, league_id: int, ts: datetime, ok: bool):
    await db.execute(
        "INSERT INTO collection_log (ts, league_id, source, endpoint, ok) VALUES ($1, $2, 1, 'gamesByChamp', $3)",
        ts, league_id, ok,
    )


async def test_check_league_returns_none_when_never_logged(db):
    assert await check_league(db, MK_X) is None


async def test_check_league_returns_age_of_last_successful_cycle(db):
    ts = datetime.now(timezone.utc) - timedelta(seconds=45)
    await log_cycle(db, league_id=MK_X, ts=ts, ok=True)
    age = await check_league(db, MK_X)
    assert 40 <= age <= 50


async def test_check_league_only_counts_ok_cycles(db):
    await log_cycle(db, league_id=MK_X, ts=datetime.now(timezone.utc) - timedelta(seconds=5), ok=False)
    assert await check_league(db, MK_X) is None  # seul un échec existe : toujours considéré muet


async def test_run_check_alerts_when_a_league_is_stale(db):
    alerter = FakeAlerter()
    await log_cycle(db, league_id=MK_X, ts=datetime.now(timezone.utc) - timedelta(seconds=500), ok=True)
    healthy = await run_check(db, alerter, [MK_X], max_age_seconds=120)
    assert healthy is False
    assert len(alerter.calls) == 1
    assert "1252965" in alerter.calls[0][2]


async def test_run_check_does_not_alert_when_all_leagues_are_fresh(db):
    alerter = FakeAlerter()
    await log_cycle(db, league_id=MK_X, ts=datetime.now(timezone.utc) - timedelta(seconds=5), ok=True)
    await log_cycle(db, league_id=MK_3, ts=datetime.now(timezone.utc) - timedelta(seconds=5), ok=True)
    healthy = await run_check(db, alerter, [MK_X, MK_3], max_age_seconds=120)
    assert healthy is True
    assert alerter.calls == []


async def test_run_check_reports_every_stale_league_in_one_alert(db):
    alerter = FakeAlerter()
    await log_cycle(db, league_id=MK_X, ts=datetime.now(timezone.utc) - timedelta(seconds=500), ok=True)
    # MK_3 : jamais enregistré du tout.
    healthy = await run_check(db, alerter, [MK_X, MK_3], max_age_seconds=120)
    assert healthy is False
    assert len(alerter.calls) == 1
    message = alerter.calls[0][2]
    assert "1252965" in message and "2282406" in message
