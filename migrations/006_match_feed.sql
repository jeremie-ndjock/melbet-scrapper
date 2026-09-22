-- 006 : suivi du fil Telegram en direct par match ("étape 12" fonctionnelle, hors du plan initial
-- à 11 étapes, demandée par l'utilisateur le 2026-09-22).
--
-- Un message Telegram par match, édité à chaque nouvelle manche plutôt que renvoyé (voir
-- src/collector/live_feed.py). Cette table retient l'identifiant du message à éditer et le nombre
-- de manches déjà publiées, pour reprendre correctement après un redémarrage sans dupliquer un
-- message ni sauter une manche.
CREATE TABLE match_feed (
    game_id             bigint      PRIMARY KEY REFERENCES events (game_id),
    chat_id             text        NOT NULL,
    message_id          bigint,                 -- NULL tant qu'aucun envoi n'a encore réussi
    last_round_notified smallint    NOT NULL DEFAULT 0,
    match_finished      boolean     NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT match_feed_round_chk CHECK (last_round_notified >= 0)
);
