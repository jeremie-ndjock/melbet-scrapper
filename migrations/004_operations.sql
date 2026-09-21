-- 004 : tables d'exploitation (journal de collecte, lettres mortes, points de reprise).

-- Un enregistrement par cycle et par appel. Indispensable avec l'option A : permet de distinguer
-- « la cote n'a pas changé » de « le collecteur était aveugle » lors de la reconstruction d'une grille.
CREATE TABLE collection_log (
    ts             timestamptz NOT NULL,
    league_id      integer     NOT NULL DEFAULT 0,     -- 0 = appel global (ex. dictionnaire)
    source         smallint    NOT NULL,               -- 1 = v3, 2 = legacy, 3 = résultats, 4 = dictionnaire
    endpoint       text        NOT NULL,
    ok             boolean     NOT NULL,
    http_status    smallint,
    latency_ms     integer,
    n_games        smallint,
    n_rows_written integer,
    error          text,
    PRIMARY KEY (ts, league_id, source, endpoint),
    CONSTRAINT collection_log_source_chk CHECK (source IN (1, 2, 3, 4))
);

SELECT create_hypertable('collection_log', 'ts', chunk_time_interval => INTERVAL '30 days');

ALTER TABLE collection_log SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'league_id, source, endpoint',
    timescaledb.compress_orderby   = 'ts'
);
SELECT add_compression_policy('collection_log', INTERVAL '14 days');

-- Réponses qu'on n'a pas pu interpréter (JSON corrompu, structure changée). Le contenu brut est du texte,
-- car un JSON invalide ne peut pas être stocké en jsonb.
CREATE TABLE dead_letter (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    source        smallint    NOT NULL,
    endpoint      text        NOT NULL,
    league_id     integer,
    error         text        NOT NULL,
    payload       text,
    payload_bytes integer
);
CREATE INDEX dead_letter_ts_idx ON dead_letter (ts DESC);

-- Points de reprise des travailleurs (ex. dernière fenêtre de résultats rattrapée).
CREATE TABLE checkpoints (
    worker     text        NOT NULL,
    key        text        NOT NULL,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (worker, key)
);
