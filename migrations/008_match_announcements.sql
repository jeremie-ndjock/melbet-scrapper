-- 008 : annonce pré-match sur Telegram, avec compte à rebours (demandé le 2026-09-22).
--
-- Distinct de `match_feed` (le suivi manche par manche, qui ne commence qu'à la première manche
-- terminée) : ceci suit le message publié AVANT le début du match (portraits des deux
-- combattants si disponibles, temps restant avant le début). Un message par match, édité
-- périodiquement (toutes les ~10 s) tant que le compte à rebours n'est pas terminé.
CREATE TABLE match_announcements (
    game_id        bigint      PRIMARY KEY REFERENCES events (game_id),
    chat_id        text        NOT NULL,
    message_id     bigint,                 -- NULL tant qu'aucun envoi n'a encore réussi
    is_photo       boolean     NOT NULL DEFAULT false,  -- true = légende à éditer (photo/album), false = texte
    last_seconds   integer,                -- dernier temps restant affiché (évite de réafficher deux fois la même valeur)
    last_edited_at timestamptz,
    done           boolean     NOT NULL DEFAULT false,  -- match démarré : plus aucune édition à faire
    created_at     timestamptz NOT NULL DEFAULT now()
);
