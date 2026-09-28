"""Prédictions en direct sur le salon Telegram « Prédiction Mortal Kombat » (Memoire.md,
sections 39 et 40).

Service séparé du collecteur (conteneur ``predictor``) : il lit la base en lecture seule toutes
les quelques secondes, prédit chaque manche avant qu'elle commence (vainqueur ; finish sur MK3 ;
durée face à la ligne principale du marché sur MKX), puis indique si chaque prédiction était
juste. Un message par match, édité au fil des manches.

Rien n'est écrit en base. Dans le volume ``ml_artifacts`` :
- ``live_state.json`` : état des messages en cours ;
- ``predictions_log.jsonl`` : journal permanent des prédictions résolues, source du bilan
  quotidien et du jugement de la règle prudente (``rules.py``) ;
- ``bilan_state.json`` : dernier bilan quotidien publié.

Modèles : les champions promus par le réentraînement quotidien (``production.py``), sinon ceux
de la dernière évaluation. Garde-fou : chaque message rappelle qu'aucun avantage de pari n'est
démontré (section 38), et affiche la probabilité du marché à côté de celle du modèle.
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
from scipy.special import logit

from . import extract, journal, production, quality
from .features import DEFAULT_LAG, intra_match, match_features
from .market import closing_quotes, decode_param, devig, main_line
from .pipelines import REQUIRED_T, _apply, _full_proba
from .targets import (FINISH_CLASSES, FINISH_DI_TO_CODE, FINISH_MARKET_T_ORDERED, G_DURATION, G_FINISH, G_WINNER,
                      LEAGUE_NAMES, MK3, MKX, T_OVER, T_P1, T_P2, T_UNDER)

log = logging.getLogger("forecasting.live")

LEAGUES = (MKX, MK3)
POLL_SECONDS = 5.0
HISTORY_REFRESH_SECONDS = 1800.0
RETRY_AFTER_FAILURE_SECONDS = 30.0
MAX_EDIT_FAILURES = 5
FORGET_AFTER_SECONDS = 3600.0
BILAN_AFTER = pd.Timedelta(minutes=5)  # après minuit UTC, le temps que les dernières manches soient résolues
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
    """Modèles de la dernière exécution d'évaluation (dossiers nommés ``<as_of>_<version>``)."""
    runs = sorted(d for d in Path(artifacts_dir).glob("*_*") if d.is_dir() and any(d.glob("*.joblib")))
    if not runs:
        return None, {}
    run = runs[-1]
    return run.name, {f.stem: joblib.load(f) for f in run.glob("*.joblib")}


def load_models(artifacts_dir: Path) -> tuple[str | None, dict]:
    """Champions de production en priorité, complétés par les modèles de la dernière évaluation
    pour les cibles qui n'ont pas (encore) de champion."""
    eval_name, models = latest_models(artifacts_dir)
    champ_sig, champions = production.load_champions(artifacts_dir)
    models = {**models, **champions}
    if not models:
        return None, {}
    return f"évaluation {eval_name} ; champions {champ_sig}", models


def _finish_of(code, di) -> str | None:
    if isinstance(code, str) and code in FINISH_NAMES:
        return code
    return FINISH_DI_TO_CODE.get(di) if isinstance(di, str) else None


def round_frame(match_feat: pd.Series, match_meta: dict, rounds_so_far: pd.DataFrame, round_no: int) -> pd.DataFrame:
    """Ligne de variables de la manche ``round_no``, à partir des manches déjà jouées du match."""
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
    return x


def predict_round(models: dict, league: int, x: pd.DataFrame) -> dict:
    """Vainqueur (P joueur 1) et, si un modèle existe, distribution du type de finish."""
    out: dict = {}
    win = models.get(f"vainqueur_{league}")
    if win is not None:
        out["p1"] = float(_apply(win["calibrateur"], _full_proba(win["modele"], x[win["variables"]], 2), True)[0, 1])
    fin = models.get(f"finish_{league}")
    if fin is not None:
        P = _apply(fin["calibrateur"], _full_proba(fin["modele"], x[fin["variables"]], len(FINISH_CLASSES)), False)[0]
        best = int(P.argmax())
        out.update(finish=FINISH_CLASSES[best], p_finish=float(P[best]), P_finish=[float(p) for p in P])
    return out


def predict_duration(models: dict, league: int, x: pd.DataFrame, line: float, p_over_market: float) -> float | None:
    """P(durée > ligne) ; le modèle utilise la cote du marché parmi ses variables."""
    dur = models.get(f"duree_{league}")
    if dur is None:
        return None
    x = x.copy()
    x["line"], x["logit_p_market"] = line, float(logit(np.clip(p_over_market, 1e-6, 1 - 1e-6)))
    return float(_apply(dur["calibrateur"], _full_proba(dur["modele"], x[dur["variables"]], 2), True)[0, 1])


def market_probs(quotes: pd.DataFrame, game_id: int, round_no: int) -> dict:
    """Probabilités du marché (marge retirée) et cotes pour une manche, à la dernière cote où
    toutes les sélections étaient proposées ensemble. Vide si le marché n'est pas (encore) publié."""
    q = quotes[(quotes["game_id"] == game_id) & quotes["g"].isin([G_WINNER, G_FINISH])]
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
        odds = f[[f"close_{t}" for t in FINISH_MARKET_T_ORDERED]].to_numpy(float)
        P, _ = devig(odds)
        out["finish"] = {c: float(p) for c, p in zip(FINISH_CLASSES, P[0])}
        out["P_finish"], out["odds_finish"] = [float(p) for p in P[0]], [float(o) for o in odds[0]]
    return out


def duration_market(quotes: pd.DataFrame, game_id: int, round_no: int) -> dict:
    """Ligne principale du marché « Durée du Round » et P(plus) marge retirée, UNIQUEMENT si ce
    marché est encore ouvert (le site le retire au début de la manche : une prédiction publiée
    après cela ne serait plus une prédiction)."""
    q = quotes[(quotes["game_id"] == game_id) & (quotes["g"] == G_DURATION)]
    if q.empty:
        return {}
    closing = closing_quotes(q, REQUIRED_T)
    closing = closing[closing["round_no"] == round_no]
    if closing.empty:
        return {}
    best = main_line(closing, T_OVER, T_UNDER).iloc[0]
    rnd, line = decode_param(q["g"], q["param"])
    last = q[(rnd == round_no) & (line == best["line"])].sort_values("ts_server").groupby("t").tail(1)
    if len(last) < 2 or last["odds"].isna().any() or last["blocked"].astype(bool).any():
        return {}
    P, _ = devig(np.array([[best[f"close_{T_UNDER}"], best[f"close_{T_OVER}"]]], float))
    return {"line": float(best["line"]), "p_over": float(P[0, 1])}


def _pct(p) -> str:
    return "—" if p is None else f"{p * 100:.0f} %"


def _line_s(line: float) -> str:
    return f"{line:.1f}".replace(".", ",")


def render_message(match: dict) -> str:
    """Texte complet du message d'un match, reconstruit à chaque fois (idempotent)."""
    p1, p2 = match["names"]
    start = pd.Timestamp(match["start"]).strftime("%H:%M")
    lines = [f"🔮 PRÉDICTIONS — {LEAGUE_NAMES[match['league']].upper()}", f"🥊 {p1} VS {p2} · début {start} UTC", ""]
    ok = {"w": 0, "f": 0, "d": 0}
    n = {"w": 0, "f": 0, "d": 0}
    for key in sorted(match["rounds"], key=int):
        r = match["rounds"][key]
        fav_is_p1 = r["p1"] >= 0.5
        p_fav = r["p1"] if fav_is_p1 else 1 - r["p1"]
        mkt = r.get("mkt_p1")
        mkt_fav = None if mkt is None else (mkt if fav_is_p1 else 1 - mkt)
        text = f"M{key} · {p1 if fav_is_p1 else p2} {_pct(p_fav)} (marché {_pct(mkt_fav)})"
        if r.get("finish"):
            text += f" · finish : {FINISH_NAMES[r['finish']]} {_pct(r['p_finish'])} (marché {_pct(r.get('mkt_finish'))})"
        over = None
        if r.get("dur_line") is not None:
            over = r["p_over"] >= 0.5
            side = "plus" if over else "moins"
            p_side = r["p_over"] if over else 1 - r["p_over"]
            m_side = r["mkt_over"] if over else 1 - r["mkt_over"]
            text += f" · durée : {side} de {_line_s(r['dur_line'])} s {_pct(p_side)} (marché {_pct(m_side)})"
        if r.get("winner") is None:
            text += " → ⏳"
        else:
            won = (r["winner"] == 1) == fav_is_p1
            n["w"], ok["w"] = n["w"] + 1, ok["w"] + won
            text += f" → {p1 if r['winner'] == 1 else p2} {'✅' if won else '❌'}"
            if r.get("finish"):
                real = r.get("finish_real")
                if real is None:
                    text += " · finish ⏳"
                else:
                    n["f"], ok["f"] = n["f"] + 1, ok["f"] + (real == r["finish"])
                    text += f" · {FINISH_NAMES[real]} {'✅' if real == r['finish'] else '❌'}"
            if over is not None:
                sec = r.get("seconds_real")
                if sec is None:
                    text += " · durée ⏳"
                else:
                    good = (sec > r["dur_line"]) == over
                    n["d"], ok["d"] = n["d"] + 1, ok["d"] + good
                    text += f" · {sec:.0f} s {'✅' if good else '❌'}"
        lines.append(text)
    if n["w"]:
        parts = [f"vainqueur {ok['w']}/{n['w']} ✅"]
        if n["f"]:
            parts.append(f"finish {ok['f']}/{n['f']} ✅")
        if n["d"]:
            parts.append(f"durée {ok['d']}/{n['d']} ✅")
        lines += ["", "Bilan : " + " · ".join(parts)]
    if match.get("done"):
        lines.append("🏁 Match terminé")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def _is_resolved(r: dict) -> bool:
    return (r.get("winner") is not None
            and (not r.get("finish") or r.get("finish_real") is not None)
            and (r.get("dur_line") is None or r.get("seconds_real") is not None))


class LivePredictor:
    def __init__(self, dsn: str, artifacts_dir: Path, sender, chat_id: str, lag: pd.Timedelta = DEFAULT_LAG):
        self.dsn, self.artifacts_dir, self.sender, self.chat_id, self.lag = dsn, Path(artifacts_dir), sender, chat_id, lag
        self.state_file = self.artifacts_dir / "live_state.json"
        self.log_file = self.artifacts_dir / "predictions_log.jsonl"
        self.bilan_file = self.artifacts_dir / "bilan_state.json"
        self.state: dict = self._load_json(self.state_file)
        self.history: dict = {}
        self.history_loaded_at = 0.0
        self.feat_cache: dict = {}
        self.models: dict = {}
        self.model_sig: str | None = None
        self.retry_at: dict = {}
        self.edit_failures: dict = {}

    @staticmethod
    def _load_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

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

    def _meta(self, gid: int, m: dict) -> dict:
        return {"game_id": gid, "start": pd.Timestamp(m["start"]), "p1_key": m["p1_key"], "p2_key": m["p2_key"]}

    async def step(self, conn) -> int:
        """Un cycle : nouveaux matchs, prédictions, résultats, messages, bilan quotidien. Retourne
        le nombre de messages de match envoyés ou édités."""
        sig, models = load_models(self.artifacts_dir)
        if sig is None:
            log.warning("aucun modèle entraîné dans %s : lancer d'abord `python -m forecasting run`", self.artifacts_dir)
            return 0
        if sig != self.model_sig:
            self.model_sig, self.models = sig, models
            log.info("modèles chargés : %s (%s)", sig, sorted(models))
        if time.monotonic() - self.history_loaded_at > HISTORY_REFRESH_SECONDS:
            await self.refresh_history(conn)

        for row in await conn.fetch(LIVE_MATCHES_SQL, list(LEAGUES)):
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
        sent = 0
        if active:
            scores = {r["game_id"]: (r["score1"], r["score2"]) for r in await conn.fetch(SCORES_SQL, active)}
            finished = {r["game_id"] for r in await conn.fetch(FINISHED_SQL, active)}
            rounds = pd.DataFrame([dict(r) for r in await conn.fetch(LIVE_ROUNDS_SQL, active)],
                                  columns=["game_id", "round_no", "winner", "seconds", "finish_code", "finish_di"])
            rounds["finish"] = [_finish_of(c, d) for c, d in zip(rounds["finish_code"], rounds["finish_di"])]
            quotes = pd.DataFrame([dict(r) for r in await conn.fetch(LIVE_QUOTES_SQL, list(LEAGUES), active,
                                                                       [G_WINNER, G_FINISH, G_DURATION])],
                                  columns=["game_id", "g", "t", "param", "odds", "blocked", "ts_server"])
            if not quotes.empty:
                quotes["odds"] = quotes["odds"].astype(float)
                quotes["param"] = quotes["param"].astype(float)
                quotes["ts_server"] = pd.to_datetime(quotes["ts_server"], utc=True)
            for gid in active:
                self._update_match(gid, scores.get(gid), gid in finished, rounds[rounds["game_id"] == gid], quotes)
            sent = await self._publish()
        self._forget_old()
        self._save_json(self.state_file, self.state)
        await self._daily_bilan()
        return sent

    def _fill_market(self, m: dict, gid: int, n: int, r: dict, quotes: pd.DataFrame, x_factory) -> None:
        """Complète ce qui manquait au moment de la prédiction (cote pas encore publiée) tant que la
        manche n'est pas jouée ; la durée n'est ajoutée que si son marché est encore ouvert."""
        if r.get("mkt_p1") is None or (r.get("finish") and r.get("mkt_P_finish") is None):
            mk = market_probs(quotes, gid, n)
            if r.get("mkt_p1") is None and "p1" in mk:
                r["mkt_p1"], m["dirty"] = mk["p1"], True
            if r.get("finish") and r.get("mkt_P_finish") is None and "P_finish" in mk:
                r["mkt_P_finish"], r["odds_finish"] = mk["P_finish"], mk["odds_finish"]
                r["mkt_finish"], m["dirty"] = mk["finish"][r["finish"]], True
        if m["league"] == MKX and r.get("dur_line") is None and f"duree_{m['league']}" in self.models:
            dm = duration_market(quotes, gid, n)
            if dm:
                p_over = predict_duration(self.models, m["league"], x_factory(), dm["line"], dm["p_over"])
                if p_over is not None:
                    r.update(dur_line=dm["line"], mkt_over=dm["p_over"], p_over=p_over)
                    m["dirty"] = True

    def _update_match(self, gid: int, score, finished: bool, rounds: pd.DataFrame, quotes: pd.DataFrame) -> None:
        m = self.state[str(gid)]
        league = m["league"]
        s1, s2 = score if score else (0, 0)
        known = rounds.set_index("round_no")
        for key, r in m["rounds"].items():
            n = int(key)
            row = known.loc[n] if n in known.index else None
            if row is not None and r.get("winner") is None and row["winner"] in (1, 2):
                r["winner"], m["dirty"] = int(row["winner"]), True
            if row is not None and r.get("finish") and r.get("finish_real") is None and row["finish"]:
                r["finish_real"], m["dirty"] = row["finish"], True
            if (row is not None and r.get("dur_line") is not None and r.get("seconds_real") is None
                    and pd.notna(row["seconds"])):
                r["seconds_real"], m["dirty"] = float(row["seconds"]), True
            if r.get("winner") is None and league in self.history:
                self._fill_market(m, gid, n, r, quotes,
                                  lambda n=n: round_frame(self._match_features(league, self._meta(gid, m)),
                                                          self._meta(gid, m), rounds, n))
            if not r.get("logged") and _is_resolved(r):
                self._log_round(m, gid, n, r)

        over = s1 >= 5 or s2 >= 5 or finished
        next_round = s1 + s2 + 1
        previous_known = all(n in known.index and known.loc[n, "winner"] in (1, 2) for n in range(1, next_round))
        if not over and str(next_round) not in m["rounds"] and previous_known and league in self.history:
            meta = self._meta(gid, m)
            x = round_frame(self._match_features(league, meta), meta, rounds, next_round)
            pred = predict_round(self.models, league, x)
            if "p1" in pred:
                m["rounds"][str(next_round)] = pred
                self._fill_market(m, gid, next_round, pred, quotes, lambda: x)
                m["dirty"] = True
        if over and all(r.get("winner") is not None for r in m["rounds"].values()):
            m["done"], m["dirty"], m["done_at"] = True, True, time.time()

    def _log_round(self, m: dict, gid: int, n: int, r: dict) -> None:
        journal.append(self.log_file, {
            "ts": pd.Timestamp.now(tz="UTC").isoformat(), "league": m["league"], "game_id": gid, "round": n,
            "p1": r["p1"], "mkt_p1": r.get("mkt_p1"), "winner": r.get("winner"),
            "P_finish": r.get("P_finish"), "mkt_P_finish": r.get("mkt_P_finish"), "odds_finish": r.get("odds_finish"),
            "finish_real": r.get("finish_real"), "dur_line": r.get("dur_line"), "p_over": r.get("p_over"),
            "mkt_over": r.get("mkt_over"), "seconds_real": r.get("seconds_real"), "modeles": self.model_sig,
        })
        r["logged"] = True

    async def _publish(self) -> int:
        """Envoie ou édite les messages modifiés. Une édition en échec est retentée plus tard ;
        un nouveau message n'est envoyé qu'après plusieurs échecs de suite (message supprimé ou
        devenu trop ancien), pour ne pas créer de doublon sur une simple coupure réseau."""
        sent = 0
        for key, m in self.state.items():
            if not m.get("dirty") or not m["rounds"] or time.monotonic() < self.retry_at.get(key, 0):
                continue
            text = render_message(m)
            first = m["message_id"] is None
            if first or self.edit_failures.get(key, 0) >= MAX_EDIT_FAILURES:
                message_id = await self.sender.send(self.chat_id, text)
            else:
                message_id = m["message_id"] if await self.sender.edit(self.chat_id, m["message_id"], text) else None
            if message_id is None:
                if not first:
                    self.edit_failures[key] = self.edit_failures.get(key, 0) + 1
                self.retry_at[key] = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
                log.warning("publication en échec pour le match %s, nouvel essai dans %.0f s", key, RETRY_AFTER_FAILURE_SECONDS)
                continue
            m["message_id"], m["dirty"] = message_id, False
            self.edit_failures.pop(key, None)
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
            m = self.state.pop(key)
            for n, r in m["rounds"].items():  # dernier filet : rien de prédit ne sort du journal
                if not r.get("logged") and r.get("winner") is not None:
                    self._log_round(m, int(key), int(n), r)
            self.feat_cache.pop(int(key), None)

    async def _daily_bilan(self, now: pd.Timestamp | None = None) -> bool:
        """Publie le bilan de la veille (UTC), une seule fois, peu après minuit."""
        now = now or pd.Timestamp.now(tz="UTC")
        yesterday = (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        meta = self._load_json(self.bilan_file)
        last = meta.get("dernier_bilan")
        if last is None:  # premier démarrage : premier bilan pour la journée en cours, demain
            self._save_json(self.bilan_file, {"dernier_bilan": yesterday})
            return False
        if last >= yesterday or now - now.normalize() < BILAN_AFTER:
            return False
        text = journal.daily_report(journal.read(self.log_file), yesterday)
        if text is not None and await self.sender.send(self.chat_id, text) is None:
            return False  # nouvel essai au cycle suivant
        self._save_json(self.bilan_file, {"dernier_bilan": yesterday})
        log.info("bilan quotidien du %s %s", yesterday, "publié" if text else "sans objet (aucune manche)")
        return text is not None


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
