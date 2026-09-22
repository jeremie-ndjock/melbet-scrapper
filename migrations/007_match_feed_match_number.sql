-- 007 : numéro du match dans la journée, pour l'affichage Telegram (demandé le 2026-09-22).
--
-- Le champ `num` du site (ex. 220241) est un identifiant interne au bookmaker, pas un compteur
-- journalier (vérifié en conditions réelles : aucune corrélation avec l'ordre ou la date des
-- matchs). Calculé nous-mêmes : le rang du match parmi tous ceux de la même ligue commençant le
-- même jour (UTC), par heure de début croissante. Figé au premier calcul (colonne, pas recalculé
-- à chaque cycle) pour que le numéro affiché ne change jamais une fois publié, même si d'autres
-- matchs de la même journée sont découverts après coup.
ALTER TABLE match_feed
    ADD COLUMN match_no_of_day smallint;
