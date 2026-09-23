-- 009 : identité d'une sélection étendue au sous-match (set), pour AI Table Tennis (2026-09-23).
--
-- Découvert en reconnaissance (Memoire.md, section 27) : contrairement à Mortal Kombat où les
-- marchés sont directement sur le match, certains sports (AI Table Tennis confirmé, probablement
-- d'autres) exposent une partie de leurs marchés par SOUS-MATCH (`subGamesForMainGame`, un par
-- set) — chacun avec son propre identifiant fourni par le site (ex. "3 Set" a son propre `id`,
-- distinct de celui du match). Sans en tenir compte, une même paire (g, t, param) pour deux sets
-- différents du même match (ex. Handicap du set 1 et Handicap du set 2) serait vue comme LA MÊME
-- sélection, avec des cotes qui s'écraseraient mutuellement de façon incorrecte.
--
-- `sub_game_id = 0` signifie « marché au niveau du match entier » (comportement de Mortal Kombat,
-- et des marchés d'AI Table Tennis qui ne sont pas propres à un set, ex. 1x2) ; une valeur non
-- nulle est l'identifiant du sous-match tel que fourni par le site.
--
-- Sûr à appliquer maintenant : la politique de compression (7 jours) n'a encore compressé aucun
-- chunk à ce stade du projet — un ALTER TABLE sur des chunks déjà compressés serait plus délicat.

ALTER TABLE odds_snapshots
    ADD COLUMN sub_game_id bigint NOT NULL DEFAULT 0;

ALTER TABLE odds_snapshots
    DROP CONSTRAINT odds_snapshots_pk;
ALTER TABLE odds_snapshots
    ADD CONSTRAINT odds_snapshots_pk PRIMARY KEY (ts_server, game_id, g, t, param, sub_game_id);

DROP VIEW odds_latest;
CREATE VIEW odds_latest AS
SELECT DISTINCT ON (game_id, g, t, param, sub_game_id)
       game_id, g, t, param, sub_game_id, league_id, ts_server, odds, blocked, is_center, round_no, line
FROM odds_snapshots
ORDER BY game_id, g, t, param, sub_game_id, ts_server DESC;
