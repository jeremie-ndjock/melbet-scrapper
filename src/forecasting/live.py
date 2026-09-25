"""Prédictions en direct sur le salon Telegram « Prédiction Mortal Kombat » (Memoire.md,
section 39).

Service séparé du collecteur (conteneur ``predictor``) : il lit la base en lecture seule toutes
les quelques secondes, prédit chaque manche avant qu'elle commence, puis indique si la prédiction
était juste. Un message par match, édité au fil des manches (même principe que le fil de match).
L'état des messages publiés est gardé dans un fichier du volume ``ml_artifacts`` : aucune
écriture en base.

Garde-fou : chaque message rappelle qu'aucun avantage de pari n'est démontré (section 38). Les
probabilités du marché (marge retirée) sont affichées à côté de celles du modèle pour que le
lecteur voie que les deux sont très proches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import asyncpg
import joblib
import numpy as np
import pandas as pd

from . import extract, quality
from .features import DEFAULT_LAG, intra_match, match_features
from .market import closing_quotes, devig
from .pipelines import REQUIRED_T, _apply, _full_proba
from .targets import (FINISH_CLASSES, FINISH_DI_TO_CODE, FINISH_MARKET_T_ORDERED, G_FINISH, G_WINNER, LEAGUE_NAMES,
                      MK3, MKX, T_P1, T_P2)

log = logging.getLogger("forecasting.live")

LEAGUES = (MKX, MK3)
POLL_SECONDS = 5.0
HISTORY_REFRESH_SECONDS = 1800.0
RETRY_AFTER_FAILURE_SECONDS = 30.0
FORGET_AFTER_SECONDS = 3600.0
FINISH_NAMES = {"R": "Regular", "F": "Fatality", "B": "Brutality", "Ba": "Babality", "Fr": "Friendship",
                "An": "Animality", "Hk": "Hara-Kiri"}
DISCLAIMER = "⚠️ Expérimental : aucun avantage de pari démontré (rapport du 25/09) — ne pas parier."

LIVE_MATCHES_SQL = """
SELECT game_id, league_id, p1_id, p2_id, p1_name, p2_name, start_ts, status
FROM events
WHERE league_id = ANY($1::int[]) AND finished_at IS NULL AND status IN ('scheduled', 'live')
  AND last_seen > now() - interval '2 minutes' AND start_ts < now() + interval '10 minutes'
"""
SCORES_SQL = """
SELECT DISTINCT ON (game_id) game_id, score1, score2
FROM game_state WHERE game_id = ANY($1::bigint[]) ORDER BY game_id, ts_server DESC
"""
FINISHED_SQL = "SELECT game_id FROM events WHERE game_id = ANY($1::bigint[]) AND finished_at IS NOT NULL"
LIVE_ROUNDS_SQL = """
SELECT game_id, round_no, winner, seconds, finish_code, finish_di
FROM round_results WHERE game_id = ANY($1::bigint[])
"""
LIVE_QUOTES_SQL = """
SELECT game_id, g, t, param, odds, blocked, ts_server
FROM odds_snapshots
WHERE league_id = ANY($1::int[]) AND game_id = ANY($2::bigint[]) AND g = ANY($3::int[])
  AND sub_game_id = 0 AND ts_server > now() - interval '40 minutes'
"""


def latest_models(artifacts_dir: Path) -> tuple[str | None, dict]:
    """Modèles de la dernière exécution officielle (dossiers nommés ``<as_of>_<version>``)."""
    runs = sorted(d for d in Path(artifacts_dir).glob("*_*") if d.is_dir() and any(d.glob("*.joblib")))
    if not runs:
        return None, {}
    run = runs[-1]
    return run.name, {f.stem: joblib.load(f) for f in run.glob("*.joblib")}


def _finish_of(code, di) -> str | None:
    if isinstance(code, str) and code in FINISH_NAMES:
        return code
    return FINISH_DI_TO_CODE.get(di) if isinstance(di, str) else None


def predict_round(models: dict, league: int, match_feat: pd.Series, match_meta: dict,
                  rounds_so_far: pd.DataFrame, round_no: int) -> dict:
    """Prédiction de la manche ``round_no`` à partir des manches déjà jouées du match."""
    known = rounds_so_far[rounds_so_far["round_no"] < round_no][["round_no", "winner", "seconds", "finish"]]
    rows = pd.concat([known, pd.DataFrame([{"round_no": round_no, "winner": np.nan, "seconds": np.nan,
                                            "finish": None}])], ignore_index=True)
    rows["game_id"] = match_meta["game_id"]
    im = intra_match(rows)
    x = im[im["round_no"] == round_no].reset_index(drop=True)
    for col, val in match_feat.items():
        if col != "game_id":
            x[col] = val
    x["p1_key"], x["p2_key"] = match_meta["p1_key"], match_meta["p2_key"]
    out: dict = {}
    win = models.get(f"vainqueur_{league}")
    if win is not None:
        P = _apply(win["calibrateur"], _full_proba(win["modele"], x[win["variables"]], 2), True)
        out["p1"] = float(P[0, 1])
    fin = models.get(f"finish_{league}")
    if fin is not None:
        P = _apply(fin["calibrateur"], _full_proba(fin["modele"], x[fin["variables"]], len(FINISH_CLASSES)), False)[0]
        best = int(P.argmax())
        out["finish"], out["p_finish"] = FINISH_CLASSES[best], float(P[best])
    return out


def market_probs(quotes: pd.DataFrame, game_id: int, round_no: int) -> dict:
    """Probabilités du marché (marge retirée) pour une manche, à la dernière cote où toutes les
    sélections étaient proposées ensemble. Vide si le marché n'est pas (encore) publié."""
    q = quotes[quotes["game_id"] == game_id]
    if q.empty:
        return {}
    closing = closing_quotes(q, REQUIRED_T)
    closing = closing[closing["round_no"] == round_no]
    out = {}
    w = closing[closing["g"] == G_WINNER]
    if not w.empty:
        P, _ = devig(w[[f"close_{T_P2}", f"close_{T_P1}"]].to_numpy(float))
        out["p1"] = float(P[0, 1])
    f = closing[closing["g"] == G_FINISH]
    if not f.empty:
        P, _ = devig(f[[f"close_{t}" for t in FINISH_MARKET_T_ORDERED]].to_numpy(float))
        out["finish"] = {c: float(p) for c, p in zip(FINISH_CLASSES, P[0])}
    return out


def _pct(p) -> str:
    return "—" if p is None else f"{p * 100:.0f} %"


def render_message(match: dict) -> str:
    """Texte complet du message d'un match, reconstruit à chaque fois (idempotent)."""
    p1, p2 = match["names"]
    start = pd.Timestamp(match["start"]).strftime("%H:%M")
    lines = [f"🔮 PRÉDICTIONS — {LEAGUE_NAMES[match['league']].upper()}", f"🥊 {p1} VS {p2} · début {start} UTC", ""]
    ok_w = ok_f = n_w = n_f = 0
    for key in sorted(match["rounds"], key=int):
        r = match["rounds"][key]
        fav_is_p1 = r["p1"] >= 0.5
        fav = p1 if fav_is_p1 else p2
        p_fav = r["p1"] if fav_is_p1 else 1 - r["p1"]
        mkt = r.get("mkt_p1")
        mkt_fav = None if mkt is None else (mkt if fav_is_p1 else 1 - mkt)
        text = f"M{key} · {fav} {_pct(p_fav)} (marché {_pct(mkt_fav)})"
        if r.get("finish"):
            text += f" · finish : {FINISH_NAMES[r['finish']]} {_pct(r['p_finish'])} (marché {_pct(r.get('mkt_finish'))})"
        if r.get("winner") is None:
            text += " → ⏳"
        else:
            won = (r["winner"] == 1) == fav_is_p1
            n_w += 1
            ok_w += won
            text += f" → {p1 if r['winner'] == 1 else p2} {'✅' if won else '❌'}"
            if r.get("finish"):
                real = r.get("finish_real")
                if real is None:
                    text += " · finish ⏳"
                else:
                    n_f += 1
                    ok_f += real == r["finish"]
                    text += f" · {FINISH_NAMES[real]} {'✅' if real == r['finish'] else '❌'}"
        lines.append(text)
    if n_w:
        lines += ["", f"Bilan : vainqueur {ok_w}/{n_w} ✅" + (f" · finish {ok_f}/{n_f} ✅" if n_f else "")]
    if match.get("done"):
        lines.append("🏁 Match terminé")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


class LivePredictor:
    def __init__(self, dsn: str, artifacts_dir: Path, sender, chat_id: str, lag: pd.Timedelta = DEFAULT_LAG):
        self.dsn, self.artifacts_dir, self.sender, self.chat_id, self.lag = dsn, Path(artifacts_dir), sender, chat_id, lag
        self.state_file = self.artifacts_dir / "live_state.json"
        self.state: dict = self._load_state()
        self.history: dict = {}
        self.history_loaded_at = 0.0
        self.feat_cache: dict = {}
        self.models: dict = {}
        self.run_name: str | None = None
        self.retry_at: dict = {}

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_file)

    async def refresh_history(self, conn) -> None:
        now = pd.Timestamp.now(tz="UTC")
        matches, rounds = await extract.load_history(conn, list(LEAGUES), now - pd.Timedelta(days=100), now)
        for league in LEAGUES:
            m, r, _ = quality.clean(matches[matches["league_id"] == league], rounds)
            self.history[league] = (m, r)
        self.history_loaded_at = time.monotonic()
        self.feat_cache.clear()
        log.info("historique rechargé : %s", ", ".join(f"{k}={len(v[0])} matchs" for k, v in self.history.items()))

    def _match_features(self, league: int, meta: dict) -> pd.Series:
        gid = meta["game_id"]
        if gid not in self.feat_cache:
            hist_m, hist_r = self.history[league]
            query = pd.DataFrame([{"game_id": gid, "league_id": league, "date_start": meta["start"],
                                   "p1_key": meta["p1_key"], "p2_key": meta["p2_key"]}])
            self.feat_cache[gid] = match_features(hist_m, hist_r, lag=self.lag, query=query).iloc[0]
        return self.feat_cache[gid]

    async def step(self, conn) -> int:
        """Un cycle : nouveaux matchs, prédictions, résultats, messages. Retourne le nombre de
        messages envoyés ou édités."""
        run_name, models = latest_models(self.artifacts_dir)
        if run_name is None:
            log.warning("aucun modèle entraîné dans %s : lancer d'abord `python -m forecasting run`", self.artifacts_dir)
            return 0
        if run_name != self.run_name:
            self.run_name, self.models = run_name, models
            log.info("modèles chargés : %s (%s)", run_name, sorted(models))
        if time.monotonic() - self.history_loaded_at > HISTORY_REFRESH_SECONDS:
            await self.refresh_history(conn)

        live = await conn.fetch(LIVE_MATCHES_SQL, list(LEAGUES))
        for row in live:
            key = str(row["game_id"])
            if key not in self.state:
                self.state[key] = {
                    "league": row["league_id"], "names": [row["p1_name"], row["p2_name"]],
                    "start": row["start_ts"].isoformat(), "rounds": {}, "message_id": None, "done": False,
                    "p1_key": str(row["p1_id"]) if row["p1_id"] is not None else f"nom:{row['p1_name']}",
                    "p2_key": str(row["p2_id"]) if row["p2_id"] is not None else f"nom:{row['p2_name']}",
                    "dirty": False, "seen": time.time(),
                }
        active = [int(k) for k, m in self.state.items() if not m["done"]]
        if not active:
            return 0
        scores = {r["game_id"]: (r["score1"], r["score2"]) for r in await conn.fetch(SCORES_SQL, active)}
        finished = {r["game_id"] for r in await conn.fetch(FINISHED_SQL, active)}
        rounds = pd.DataFrame([dict(r) for r in await conn.fetch(LIVE_ROUNDS_SQL, active)],
                              columns=["game_id", "round_no", "winner", "seconds", "finish_code", "finish_di"])
        rounds["finish"] = [_finish_of(c, d) for c, d in zip(rounds["finish_code"], rounds["finish_di"])]
        quotes = pd.DataFrame([dict(r) for r in await conn.fetch(LIVE_QUOTES_SQL, list(LEAGUES), active,
                                                                   [G_WINNER, G_FINISH])],
                              columns=["game_id", "g", "t", "param", "odds", "blocked", "ts_server"])
        if not quotes.empty:
            quotes["odds"] = quotes["odds"].astype(float)
            quotes["param"] = quotes["param"].astype(float)
            quotes["ts_server"] = pd.to_datetime(quotes["ts_server"], utc=True)

        for gid in active:
            self._update_match(gid, scores.get(gid), gid in finished, rounds[rounds["game_id"] == gid], quotes)
        sent = await self._publish()
        self._forget_old()
        self._save_state()
        return sent

    def _update_match(self, gid: int, score, finished: bool, rounds: pd.DataFrame, quotes: pd.DataFrame) -> None:
        m = self.state[str(gid)]
        league = m["league"]
        s1, s2 = score if score else (0, 0)
        done_rounds = s1 + s2
        known = rounds.set_index("round_no")
        for key, r in m["rounds"].items():
            n = int(key)
            if n in known.index and r.get("winner") is None and known.loc[n, "winner"] in (1, 2):
                r["winner"] = int(known.loc[n, "winner"])
                m["dirty"] = True
            if r.get("finish") and r.get("finish_real") is None and n in known.index and known.loc[n, "finish"]:
                r["finish_real"] = known.loc[n, "finish"]
                m["dirty"] = True
            if r.get("winner") is None and (r.get("mkt_p1") is None or (r.get("finish") and r.get("mkt_finish") is None)):
                mk = market_probs(quotes, gid, n)
                if r.get("mkt_p1") is None and "p1" in mk:
                    r["mkt_p1"] = mk["p1"]
                    m["dirty"] = True
                if r.get("finish") and r.get("mkt_finish") is None and "finish" in mk:
                    r["mkt_finish"] = mk["finish"][r["finish"]]
                    m["dirty"] = True

        over = s1 >= 5 or s2 >= 5 or finished
        next_round = done_rounds + 1
        previous_known = all(n in known.index and known.loc[n, "winner"] in (1, 2) for n in range(1, next_round))
        if not over and str(next_round) not in m["rounds"] and previous_known and league in self.history:
            meta = {"game_id": gid, "start": pd.Timestamp(m["start"]), "p1_key": m["p1_key"], "p2_key": m["p2_key"]}
            pred = predict_round(self.models, league, self._match_features(league, meta), meta, rounds, next_round)
            if "p1" in pred:
                mk = market_probs(quotes, gid, next_round)
                pred["mkt_p1"] = mk.get("p1")
                if pred.get("finish"):
                    pred["mkt_finish"] = mk.get("finish", {}).get(pred["finish"])
                m["rounds"][str(next_round)] = pred
                m["dirty"] = True
        if over and all(r.get("winner") is not None for r in m["rounds"].values()):
            m["done"], m["dirty"], m["done_at"] = True, True, time.time()

    async def _publish(self) -> int:
        sent = 0
        for key, m in self.state.items():
            if not m.get("dirty") or not m["rounds"] or time.monotonic() < self.retry_at.get(key, 0):
                continue
            first = m["message_id"] is None
            message_id = await self.sender.send_or_edit(self.chat_id, m["message_id"], render_message(m))
            if message_id is None:
                self.retry_at[key] = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
                log.warning("publication en échec pour le match %s, nouvel essai dans %.0f s", key, RETRY_AFTER_FAILURE_SECONDS)
                continue
            m["message_id"], m["dirty"] = message_id, False
            sent += 1
            if first:
                log.info("nouveau message de prédiction : match %s (%s)", key, " VS ".join(m["names"]))
            if m["done"]:
                log.info("match %s terminé : %d manche(s) prédite(s)", key, len(m["rounds"]))
        return sent

    def _forget_old(self) -> None:
        now = time.time()
        for key in [k for k, m in self.state.items()
                    if (m["done"] and now - m.get("done_at", now) > FORGET_AFTER_SECONDS)
                    or (not m["rounds"] and now - m.get("seen", now) > FORGET_AFTER_SECONDS)]:
            del self.state[key]
            self.feat_cache.pop(int(key), None)


async def run_forever(dsn: str, artifacts_dir: Path, sender, chat_id: str, lag: pd.Timedelta = DEFAULT_LAG,
                      stop: asyncio.Event | None = None) -> None:
    """Boucle permanente, qui ne s'arrête jamais sur une erreur transitoire (base ou réseau) : elle
    se reconnecte et reprend au cycle suivant."""
    predictor = LivePredictor(dsn, artifacts_dir, sender, chat_id, lag)
    conn = None
    while stop is None or not stop.is_set():
        try:
            if conn is None or conn.is_closed():
                conn = await extract.connect_readonly(dsn)
            await predictor.step(conn)
        except (asyncpg.PostgresError, OSError, asyncio.TimeoutError) as exc:
            log.error("cycle de prédiction en échec (%s : %s), nouvel essai", type(exc).__name__, exc)
            if conn is not None:
                await conn.close()
            conn = None
        await asyncio.sleep(POLL_SECONDS)
    if conn is not None:
        await conn.close()
