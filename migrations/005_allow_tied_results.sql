-- 005 : autorise un résultat final à égalité.
--
-- Hypothèse de la migration 002 invalidée par l'observation réelle (essai en direct du
-- 2026-09-21, étape 6) : un match Mortal Kombat 3 s'est terminé 2:2 (probablement une
-- interruption ou un incident technique). La contrainte d'origine rejetait silencieusement ce
-- résultat via une erreur applicative (voir results.py), le perdant purement et simplement.
-- `winner` devient NULL exactement quand les scores sont égaux.

ALTER TABLE results
    ALTER COLUMN winner DROP NOT NULL,
    DROP CONSTRAINT results_winner_chk,
    DROP CONSTRAINT results_scores_chk;

ALTER TABLE results
    ADD CONSTRAINT results_scores_chk CHECK (final_score1 >= 0 AND final_score2 >= 0),
    ADD CONSTRAINT results_winner_chk CHECK (winner IS NULL OR winner IN (1, 2)),
    ADD CONSTRAINT results_winner_tie_chk CHECK ((winner IS NULL) = (final_score1 = final_score2));
