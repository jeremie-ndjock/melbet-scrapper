-- 003 : historique des cotes (hypertable), compression et vue de l'état courant.
--
-- Une ligne = un changement de cote, de verrou ou de présence d'une sélection (option A).
-- Une sélection retirée est une ligne avec odds NULL. On n'écrase jamais une ligne existante.
-- Identité d'une sélection : (game_id, g, t, param). param = 0 signifie « sans paramètre ».

CREATE TABLE odds_snapshots (
    ts_server    timestamptz   NOT NULL,        -- horodatage serveur du relevé (updateTs)
    game_id      bigint        NOT NULL REFERENCES events (game_id),
    g            integer       NOT NULL,        -- groupe de marché
    t            integer       NOT NULL,        -- type de sélection
    param        numeric(12,4) NOT NULL DEFAULT 0,
    league_id    integer       NOT NULL REFERENCES leagues (league_id),
    collected_at timestamptz   NOT NULL,        -- horodatage de collecte (notre horloge)
    odds         numeric(8,3),                  -- NULL = sélection retirée
    blocked      boolean       NOT NULL DEFAULT false,
    is_center    boolean       NOT NULL DEFAULT false,
    round_no     smallint,                      -- round visé, si décodé
    line         numeric(6,2),                  -- ligne (total, durée), si décodée
    source       smallint      NOT NULL,        -- 1 = v3, 2 = legacy
    latency_ms   integer,
    CONSTRAINT odds_snapshots_pk     PRIMARY KEY (ts_server, game_id, g, t, param),
    CONSTRAINT odds_snapshots_odds_chk   CHECK (odds IS NULL OR odds > 0),
    CONSTRAINT odds_snapshots_source_chk CHECK (source IN (1, 2))
);

SELECT create_hypertable('odds_snapshots', 'ts_server', chunk_time_interval => INTERVAL '7 days');

CREATE INDEX odds_snapshots_game_idx   ON odds_snapshots (game_id, ts_server DESC);
CREATE INDEX odds_snapshots_league_idx ON odds_snapshots (league_id, ts_server DESC);

ALTER TABLE odds_snapshots SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'league_id, g, t',
    timescaledb.compress_orderby   = 'game_id, param, ts_server'
);
SELECT add_compression_policy('odds_snapshots', INTERVAL '7 days');

-- Dernière ligne connue de chaque sélection. À utiliser avec un filtre sur game_id
-- (relecture de l'état au redémarrage) : sans filtre, la vue parcourt tout l'historique.
CREATE VIEW odds_latest AS
SELECT DISTINCT ON (game_id, g, t, param)
       game_id, g, t, param, league_id, ts_server, odds, blocked, is_center, round_no, line
FROM odds_snapshots
ORDER BY game_id, g, t, param, ts_server DESC;
