"""Service de prédictions en direct contre une vraie base : un match suivi manche par manche,
avec un expéditeur Telegram factice (aucun vrai message envoyé)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import joblib
import numpy as np

from forecasting import extract, live
from forecasting.features import FIGHTER_CATS, WINNER_NUM

MKX = 1252965
NOW = datetime.now(timezone.utc).replace(microsecond=0)
LIVE_GAME = 999


class ConstModel:
    def __init__(self, probs):
        self.probs = np.asarray(probs, float)
        self.classes_ = np.arange(len(probs))

    def predict_proba(self, X):
        return np.tile(self.probs, (len(X), 1))


class Identity:
    def transform(self, p):
        return p


class FakeSender:
    def __init__(self):
        self.sent, self.edited = [], []

    async def send_or_edit(self, chat_id, message_id, text):
        if message_id is None:
            self.sent.append(text)
            return 100 + len(self.sent)
        self.edited.append(text)
        return message_id


async def _seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        # Historique : trois matchs terminés entre les deux mêmes combattants.
        for i in range(3):
            gid, start = 10 + i, NOW - timedelta(hours=3 - i)
            await conn.execute(
                "INSERT INTO results (game_id, league_id, p1_id, p2_id, p1_name, p2_name, final_score1, final_score2,"
                " winner, score_raw, date_start) VALUES ($1, $2, 1, 2, 'Goro', 'Ermac', 5, 0, 1, '5:0', $3)", gid, MKX, start)
            await conn.executemany("INSERT INTO round_results (game_id, round_no, winner, finish_code) VALUES ($1, $2, 1, 'F')",
                                   [(gid, n) for n in range(1, 6)])
        await conn.execute(
            "INSERT INTO events (game_id, league_id, p1_id, p2_id, p1_name, p2_name, start_ts, status, first_seen, last_seen)"
            " VALUES ($1, $2, 1, 2, 'Goro', 'Ermac', $3, 'live', $3, $4)", LIVE_GAME, MKX, NOW - timedelta(minutes=2), NOW)
        await conn.execute("INSERT INTO game_state (game_id, ts_server, collected_at, period, score1, score2) VALUES ($1, $2, $2, 2, 1, 0)",
                           LIVE_GAME, NOW - timedelta(seconds=20))
        await conn.execute("INSERT INTO round_results (game_id, round_no, winner, seconds, finish_di) VALUES ($1, 1, 1, 25, 'Fatality')",
                           LIVE_GAME)
        await conn.executemany(
            "INSERT INTO odds_snapshots (ts_server, game_id, g, t, param, league_id, collected_at, odds, source)"
            " VALUES ($1, $2, 1050, $3, 2, $4, $1, $5, 1)",
            [(NOW - timedelta(seconds=10), LIVE_GAME, 2140, MKX, 1.5), (NOW - timedelta(seconds=10), LIVE_GAME, 2141, MKX, 2.6)])
    finally:
        await conn.close()


async def test_live_match_is_predicted_then_scored_round_by_round(db_dsn, tmp_path):
    await _seed(db_dsn)
    run_dir = tmp_path / "20260925T1500Z_test"
    run_dir.mkdir()
    joblib.dump({"nom": "test", "modele": ConstModel([0.35, 0.65]), "calibrateur": Identity(),
                 "variables": WINNER_NUM + FIGHTER_CATS}, run_dir / f"vainqueur_{MKX}.joblib")
    sender = FakeSender()
    predictor = live.LivePredictor(db_dsn, tmp_path, sender, "-1")
    conn = await extract.connect_readonly(db_dsn)
    writer = await asyncpg.connect(db_dsn)
    try:
        assert await predictor.step(conn) == 1
        first = sender.sent[0]
        assert "🔮 PRÉDICTIONS — MORTAL KOMBAT X" in first and "Goro VS Ermac" in first
        assert "M2 · Goro 65 % (marché 63 %) → ⏳" in first  # marché 1,5 / 2,6 marge retirée ≈ 63,4 %
        assert await predictor.step(conn) == 0  # rien de neuf : aucune édition inutile

        # Manche 2 gagnée par Ermac : la prédiction est notée, la manche 3 est prédite.
        await writer.execute("INSERT INTO round_results (game_id, round_no, winner, seconds, finish_di) VALUES ($1, 2, 2, 30, 'Regular')", LIVE_GAME)
        await writer.execute("INSERT INTO game_state (game_id, ts_server, collected_at, period, score1, score2) VALUES ($1, $2, $2, 3, 1, 1)",
                             LIVE_GAME, NOW)
        assert await predictor.step(conn) == 1
        edited = sender.edited[-1]
        assert "M2 · Goro 65 % (marché 63 %) → Ermac ❌" in edited and "M3 · Goro 65 % (marché —) → ⏳" in edited
        assert "Bilan : vainqueur 0/1 ✅" in edited

        # Fin du match : dernière manche notée, message clos, l'état survit à un redémarrage.
        await writer.execute("INSERT INTO round_results (game_id, round_no, winner) VALUES ($1, 3, 1)", LIVE_GAME)
        await writer.execute("UPDATE events SET finished_at = $2, status = 'finished' WHERE game_id = $1", LIVE_GAME, NOW)
        assert await predictor.step(conn) == 1
        assert "🏁 Match terminé" in sender.edited[-1] and "Bilan : vainqueur 1/2 ✅" in sender.edited[-1]
        restarted = live.LivePredictor(db_dsn, tmp_path, sender, "-1")
        assert restarted.state[str(LIVE_GAME)]["done"] is True
        assert await restarted.step(conn) == 0
    finally:
        await conn.close()
        await writer.close()


async def test_without_trained_models_nothing_is_published(db_dsn, tmp_path):
    sender = FakeSender()
    conn = await extract.connect_readonly(db_dsn)
    try:
        assert await live.LivePredictor(db_dsn, tmp_path, sender, "-1").step(conn) == 0
    finally:
        await conn.close()
    assert sender.sent == []
