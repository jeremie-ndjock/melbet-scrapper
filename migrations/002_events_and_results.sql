-- 002 : matchs, état des matchs, résultats officiels et détail des rounds.

CREATE TABLE events (
    game_id     bigint      PRIMARY KEY,
    league_id   integer     NOT NULL REFERENCES leagues (league_id),
    feed_id     text,                       -- identifiant du flux vidéo (ex. xgame7_55260825)
    match_no    bigint,                     -- numéro de match côté bookmaker
    p1_id       integer,
    p2_id       integer,
    p1_name     text        NOT NULL,
    p2_name     text        NOT NULL,
    start_ts    timestamptz NOT NULL,
    status      text        NOT NULL DEFAULT 'scheduled',
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL,
    finished_at timestamptz,
    CONSTRAINT events_status_chk CHECK (status IN ('scheduled', 'live', 'finished', 'vanished')),
    CONSTRAINT events_seen_chk   CHECK (last_seen >= first_seen)
);
CREATE INDEX events_league_start_idx ON events (league_id, start_ts DESC);
CREATE INDEX events_open_idx         ON events (status) WHERE status IN ('scheduled', 'live');

-- Écrit uniquement quand la période ou le score change (environ 15 à 20 lignes par match).
CREATE TABLE game_state (
    game_id      bigint      NOT NULL REFERENCES events (game_id),
    ts_server    timestamptz NOT NULL,
    collected_at timestamptz NOT NULL,
    period       smallint    NOT NULL,
    score1       smallint    NOT NULL,
    score2       smallint    NOT NULL,
    elapsed_s    integer,
    status_text  text,
    PRIMARY KEY (game_id, ts_server),
    CONSTRAINT game_state_values_chk CHECK (period >= 0 AND score1 >= 0 AND score2 >= 0)
);

-- Résultats officiels. Autonome (sans clé étrangère vers events) car le rattrapage de l'historique
-- concerne des matchs jamais vus en direct.
CREATE TABLE results (
    game_id      bigint      PRIMARY KEY,
    league_id    integer     NOT NULL REFERENCES leagues (league_id),
    p1_id        integer,
    p2_id        integer,
    p1_name      text        NOT NULL,
    p2_name      text        NOT NULL,
    final_score1 smallint    NOT NULL,
    final_score2 smallint    NOT NULL,
    winner       smallint    NOT NULL,
    score_raw    text        NOT NULL,      -- chaîne brute, ex. 5:3(0:1 F ,M- / M-; ...)
    date_start   timestamptz NOT NULL,
    source       smallint    NOT NULL DEFAULT 3,   -- 3 = service de résultats
    captured_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT results_winner_chk CHECK (winner IN (1, 2)),
    CONSTRAINT results_scores_chk CHECK (final_score1 >= 0 AND final_score2 >= 0 AND final_score1 <> final_score2)
);
CREATE INDEX results_league_date_idx ON results (league_id, date_start DESC);

-- Un round par ligne. Alimenté par le tableau des rounds (durée, type de finish) puis complété par
-- les résultats officiels (code de finish, Mercy) : les champs vides se remplissent, sans écraser.
CREATE TABLE round_results (
    game_id     bigint      NOT NULL,
    round_no    smallint    NOT NULL,
    winner      smallint,
    seconds     smallint,
    finish_di   text,                       -- libellé du tableau des rounds (Regular, Fatality, ...)
    finish_code text,                       -- code des résultats (R, F, B, Ba, Fr, An, Hk, ...)
    wt          text,                       -- champ WT brut, signification non établie
    fw          boolean,
    mercy_p1    boolean,
    mercy_p2    boolean,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, round_no),
    CONSTRAINT round_results_round_chk  CHECK (round_no >= 1),
    CONSTRAINT round_results_winner_chk CHECK (winner IS NULL OR winner IN (1, 2))
);
