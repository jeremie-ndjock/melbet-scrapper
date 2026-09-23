-- 010 : ajoute les deux salles AI Table Tennis comme ligues suivies (Memoire.md, section 27).
--
-- Le pilotage réel de ce qui est effectivement collecté reste config/leagues.yaml ; cette ligne
-- ne fait que satisfaire la contrainte de clé étrangère d'`events`/`odds_snapshots` vers
-- `leagues`, comme pour Mortal Kombat X/3 à la migration 001.
INSERT INTO leagues (league_id, name, sport_id) VALUES
    (3066896, 'AI Table Tennis Prague', 10),
    (3066897, 'AI Table Tennis Goa', 10)
ON CONFLICT (league_id) DO NOTHING;
