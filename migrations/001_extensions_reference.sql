-- 001 : extension TimescaleDB et données de référence (ligues, dictionnaire des marchés).

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE leagues (
    league_id  integer     PRIMARY KEY,
    name       text        NOT NULL,
    sport_id   integer     NOT NULL,
    enabled    boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Ligues retenues (le pilotage réel se fait par config/leagues.yaml ; ces lignes satisfont les clés étrangères).
INSERT INTO leagues (league_id, name, sport_id) VALUES
    (1252965, 'Mortal Kombat X', 103),
    (2282406, 'Mortal Kombat 3', 103)
ON CONFLICT (league_id) DO NOTHING;

-- Libellés officiels des marchés (dictionnaire du CDN), rechargés périodiquement.
CREATE TABLE markets_dict (
    group_id    integer     NOT NULL,
    type_id     integer     NOT NULL,
    group_label text        NOT NULL,
    type_label  text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, type_id)
);
