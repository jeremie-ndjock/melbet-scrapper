"""Ligne de commande : ``python -m forecasting check-data`` ou ``python -m forecasting run``.

Conçu pour tourner dans le conteneur ``ml`` du VPS (docker-compose.yml, profil « ml »), avec
une connexion en lecture seule à la base de production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import joblib
import pandas as pd

from collector.observability.logging_setup import configure_logging
from collector.telegram_feed import MatchFeedSender

from . import extract, live, notify, quality
from .pipelines import Config, run_all
from .report import write_report
from .targets import G_DURATION, G_FINISH, G_WINNER, MK3, MKX
from .trials import TrialRegistry

log = logging.getLogger("forecasting")
LEAGUES = (MKX, MK3)
ODDS_GROUPS = {MKX: [G_WINNER, G_DURATION], MK3: [G_WINNER, G_FINISH]}
HISTORY_DAYS = 100  # couvre le rattrapage des résultats (backfill_days = 90)


def default_as_of(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """Maintenant − 3 h, arrondi à l'heure : laisse à la réconciliation (toutes les 5 min, sur
    2 h) le temps de compléter les résultats récents, et rend l'exécution reproductible."""
    now = now or pd.Timestamp.now(tz="UTC")
    return (now - pd.Timedelta(hours=3)).floor("h")


async def gather(dsn: str, as_of: pd.Timestamp, with_odds: bool = True) -> tuple[dict, dict, pd.Timedelta]:
    since = as_of - pd.Timedelta(days=HISTORY_DAYS)
    conn = await extract.connect_readonly(dsn)
    try:
        matches, rounds = await extract.load_history(conn, list(LEAGUES), since, as_of)
        events = await extract.load_events(conn, list(LEAGUES), since, as_of)
        lag, lag_info = quality.match_lag(events)
        data, report = {}, {"delai_disponibilite": lag_info}
        for league in LEAGUES:
            m, r, q = quality.clean(matches[matches["league_id"] == league], rounds,
                                    events[events["league_id"] == league])
            q["couverture_duree_par_manche"] = quality.duration_coverage(r)
            d = {"matches": m, "rounds": r}
            if with_odds:
                w_start = await extract.first_odds_ts(conn, league, G_WINNER, as_of)
                if w_start is None:
                    continue
                w_start = pd.Timestamp(w_start)
                d["w_start"] = w_start
                d["odds"] = await extract.load_odds(conn, league, ODDS_GROUPS[league], w_start, as_of)
                d["round_ends"] = await extract.load_round_ends(conn, league, w_start, as_of)
                q["debut_cotes"] = w_start.isoformat()
                q["lignes_de_cotes"] = int(len(d["odds"]))
            report[str(league)] = q
            data[league] = d
        return data, report, lag
    finally:
        await conn.close()


def _save_artifacts(artifacts_dir: Path, run_id: str, store: dict, meta: dict) -> None:
    """Modèles retenus et calibrateurs (volume Docker ``ml_artifacts``, jamais versionnés),
    réutilisables par le futur réentraînement quotidien champion/challenger (plan, section 12)."""
    target = artifacts_dir / run_id
    target.mkdir(parents=True, exist_ok=True)
    for key, obj in store.items():
        joblib.dump({**obj, **meta}, target / f"{key}.joblib")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forecasting")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check-data", "run"):
        p = sub.add_parser(name)
        p.add_argument("--as-of", help="date de référence ISO (UTC), défaut : maintenant − 3 h arrondi à l'heure")
    sub.choices["run"].add_argument("--target", default="all", choices=["all", "winner", "finish", "duration"])
    sub.choices["run"].add_argument("--reports", default=os.environ.get("FORECAST_REPORTS", "/app/reports"))
    sub.choices["run"].add_argument("--artifacts", default=os.environ.get("FORECAST_ARTIFACTS", "/app/artifacts"))
    sub.choices["run"].add_argument("--no-lgbm", action="store_true", help="régression logistique seule (plus rapide)")
    sub.choices["run"].add_argument("--no-telegram", action="store_true", help="ne pas envoyer le bilan sur Telegram")
    live_p = sub.add_parser("live", help="prédictions en direct sur le salon Telegram de prédiction")
    live_p.add_argument("--artifacts", default=os.environ.get("FORECAST_ARTIFACTS", "/app/artifacts"))
    args = parser.parse_args(argv)

    configure_logging()
    dsn = os.environ["DATABASE_URL"]
    if args.cmd == "live":
        token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get(notify.CHAT_ENV)
        if not token or not chat_id:
            log.error("TELEGRAM_BOT_TOKEN et %s sont nécessaires pour les prédictions en direct", notify.CHAT_ENV)
            return 2
        log.info("prédictions en direct démarrées (salon %s)", chat_id)
        asyncio.run(live.run_forever(dsn, Path(args.artifacts), MatchFeedSender(token), chat_id))
        return 0
    as_of = pd.Timestamp(args.as_of, tz="UTC") if args.as_of else default_as_of()

    if args.cmd == "check-data":
        _, report, lag = asyncio.run(gather(dsn, as_of))
        print(json.dumps({"as_of": as_of.isoformat(), "lag_minutes": lag.total_seconds() / 60, **report},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    commit = os.environ.get("GIT_COMMIT", "inconnu")
    data, report, lag = asyncio.run(gather(dsn, as_of))
    cfg = Config(lag=lag, use_lgbm=not args.no_lgbm)
    reports_dir = Path(args.reports)
    registry = TrialRegistry(reports_dir / "trials.jsonl")
    meta = {"as_of": as_of.isoformat(), "git_commit": commit}
    targets = {"winner", "finish", "duration"} if args.target == "all" else {args.target}
    log.info("évaluation lancée (as_of=%s, cibles=%s)", as_of.isoformat(), sorted(targets))
    store: dict = {}
    results = run_all(data, as_of=as_of, cfg=cfg, registry=registry, meta=meta, targets=targets, store=store)
    run_id = f"{as_of.strftime('%Y%m%dT%H%MZ')}_{commit}"
    run = {**meta, "lag_minutes": lag.total_seconds() / 60, "qualite": report, "resultats": results}
    out = write_report(reports_dir / run_id, run)
    _save_artifacts(Path(args.artifacts), run_id, store, meta)
    for res in results:
        log.info("%s : %s", res["titre"], res["verdict"])
    log.info("rapport écrit dans %s", out)
    if not args.no_telegram and asyncio.run(notify.send_summary(run)):
        log.info("bilan envoyé sur le salon Telegram de prédiction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
