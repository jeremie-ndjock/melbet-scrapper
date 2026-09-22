"""Requêtes SQL du stockage, centralisées pour être réutilisées par le writer et les tests.

Toutes les écritures sont idempotentes : rejouer un lot (après un redémarrage, par exemple) ne
crée pas de doublon et ne fait jamais reculer un état.
"""
from __future__ import annotations

# Cotes : ajout seul. Rejouer la même ligne ne change rien (jamais de mise à jour).
INSERT_ODDS_SNAPSHOT = """
INSERT INTO odds_snapshots
    (ts_server, game_id, g, t, param, league_id, collected_at,
     odds, blocked, is_center, round_no, line, source, latency_ms)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
ON CONFLICT (ts_server, game_id, g, t, param) DO NOTHING
"""

# Match : first_seen ne change jamais ; last_seen ne recule jamais ; un match terminé ne redevient pas « live ».
UPSERT_EVENT = """
INSERT INTO events
    (game_id, league_id, feed_id, match_no, p1_id, p2_id, p1_name, p2_name,
     start_ts, status, first_seen, last_seen, finished_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11, $12)
ON CONFLICT (game_id) DO UPDATE SET
    status      = CASE WHEN events.status IN ('finished', 'vanished') THEN events.status ELSE EXCLUDED.status END,
    last_seen   = GREATEST(events.last_seen, EXCLUDED.last_seen),
    finished_at = COALESCE(events.finished_at, EXCLUDED.finished_at)
"""

# État du match : ajout seul.
INSERT_GAME_STATE = """
INSERT INTO game_state
    (game_id, ts_server, collected_at, period, score1, score2, elapsed_s, status_text)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (game_id, ts_server) DO NOTHING
"""

# Round : deux sources se complètent (tableau des rounds, puis résultats officiels).
# Un champ déjà renseigné n'est jamais écrasé ; un champ vide est rempli.
UPSERT_ROUND_RESULT = """
INSERT INTO round_results
    (game_id, round_no, winner, seconds, finish_di, finish_code, wt, fw, mercy_p1, mercy_p2)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (game_id, round_no) DO UPDATE SET
    winner      = COALESCE(round_results.winner,      EXCLUDED.winner),
    seconds     = COALESCE(round_results.seconds,     EXCLUDED.seconds),
    finish_di   = COALESCE(round_results.finish_di,   EXCLUDED.finish_di),
    finish_code = COALESCE(round_results.finish_code, EXCLUDED.finish_code),
    wt          = COALESCE(round_results.wt,          EXCLUDED.wt),
    fw          = COALESCE(round_results.fw,          EXCLUDED.fw),
    mercy_p1    = COALESCE(round_results.mercy_p1,    EXCLUDED.mercy_p1),
    mercy_p2    = COALESCE(round_results.mercy_p2,    EXCLUDED.mercy_p2),
    updated_at  = now()
"""

# Résultat officiel : le premier enregistrement fait foi (rejouer ne change rien).
INSERT_RESULT = """
INSERT INTO results
    (game_id, league_id, p1_id, p2_id, p1_name, p2_name,
     final_score1, final_score2, winner, score_raw, date_start, source)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (game_id) DO NOTHING
"""

# Relecture de l'état courant des sélections de matchs donnés (redémarrage du collecteur).
LATEST_ODDS_FOR_GAMES = """
SELECT game_id, g, t, param, odds, blocked, is_center, ts_server
FROM odds_latest
WHERE game_id = ANY($1::bigint[])
"""

# Dictionnaire des marchés : table de référence, mise à jour (pas d'historique, contrairement aux cotes).
UPSERT_MARKET_LABEL = """
INSERT INTO markets_dict (group_id, type_id, group_label, type_label, updated_at)
VALUES ($1, $2, $3, $4, now())
ON CONFLICT (group_id, type_id) DO UPDATE SET
    group_label = EXCLUDED.group_label,
    type_label  = EXCLUDED.type_label,
    updated_at  = now()
"""

# Marchés vus dans les cotes mais jamais résolus dans le dictionnaire (voir dictionary.py).
SELECT_UNLABELED_MARKETS = """
SELECT DISTINCT o.g, o.t
FROM odds_snapshots o
LEFT JOIN markets_dict d ON d.group_id = o.g AND d.type_id = o.t
WHERE d.group_id IS NULL
"""

# Point de reprise du rattrapage des résultats (une clé par ligue, voir results.py).
UPSERT_CHECKPOINT = """
INSERT INTO checkpoints (worker, key, value)
VALUES ($1, $2, $3::jsonb)
ON CONFLICT (worker, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""
SELECT_CHECKPOINT = "SELECT value FROM checkpoints WHERE worker = $1 AND key = $2"

# Journal de collecte : un cycle réussi ou échoué. Indispensable avec l'option A (voir Memoire.md,
# section 16) : sans lui, on ne peut pas distinguer « la cote n'a pas changé » de « le collecteur
# était aveugle » pour reconstruire une grille régulière. `ON CONFLICT DO NOTHING` par sécurité
# (collision improbable de l'horodatage à la microseconde), jamais attendu en pratique.
INSERT_COLLECTION_LOG = """
INSERT INTO collection_log (ts, league_id, source, endpoint, ok, http_status, latency_ms, n_games, n_rows_written, error)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (ts, league_id, source, endpoint) DO NOTHING
"""

# Réponse en échec de validation de schéma (voir transport/errors.py ParserError et scheduler.py).
# Le payload est tronqué à l'écriture (voir scheduler.py) : payload_bytes garde la taille d'origine.
INSERT_DEAD_LETTER = """
INSERT INTO dead_letter (source, endpoint, league_id, error, payload, payload_bytes)
VALUES ($1, $2, $3, $4, $5, $6)
"""
