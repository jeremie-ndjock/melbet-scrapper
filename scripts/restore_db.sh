#!/usr/bin/env bash
# Restauration d'une sauvegarde produite par `backup_db.sh`, dans une base VIDE.
#
# Usage : ./scripts/restore_db.sh backups/odds_2026-09-22_030000.sql.gz
#
# Destiné à un scénario de reprise après sinistre (nouveau VPS, volume `db_data` perdu) ou à la
# vérification périodique qu'une sauvegarde est bien exploitable (recommandé : la tester de temps
# en temps sur une base jetable, une sauvegarde jamais restaurée n'est pas une vraie garantie).
# N'écrase jamais une base contenant déjà des données : demande confirmation explicite.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

file="${1:?usage : $0 <fichier .sql.gz>}"
[ -f "$file" ] || { echo "fichier introuvable : $file" >&2; exit 1; }

# Voir backup_db.sh : normalise une éventuelle fin de ligne Windows (CRLF) avant de sourcer.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source <(tr -d '\r' < .env)
    set +a
fi
: "${POSTGRES_USER:?POSTGRES_USER manquant (voir .env)}"
: "${POSTGRES_DB:?POSTGRES_DB manquant (voir .env)}"

existing="$(docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
if [ "${existing:-0}" -gt 0 ]; then
    echo "ATTENTION : la base '$POSTGRES_DB' contient déjà $existing table(s)." >&2
    read -r -p "Continuer et restaurer par-dessus quand même ? [taper 'oui' pour confirmer] " reply
    [ "$reply" = "oui" ] || { echo "annulé."; exit 1; }
fi

echo "[$(date -u +%FT%TZ)] restauration de $file dans '$POSTGRES_DB'..."

# Indispensable avec TimescaleDB : un `pg_dump` classique restauré tel quel échoue (contraintes de
# clé étrangère vérifiées sur des chunks d'hypertable pas encore repartitionnés au moment de leur
# création). `timescaledb_pre_restore()` désactive temporairement les tâches de fond et les
# vérifications concernées ; `timescaledb_post_restore()` les rétablit une fois les données en
# place. Défaut réel trouvé et corrigé à l'étape 10 en testant une vraie restauration, pas
# supposé en lisant seulement la documentation de `pg_dump`.
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT timescaledb_pre_restore();"
gunzip -c "$file" | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT timescaledb_post_restore();"
echo "[$(date -u +%FT%TZ)] restauration terminée."
