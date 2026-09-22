#!/usr/bin/env bash
# Sauvegarde quotidienne de la base (pg_dump compressé, horodaté).
#
# Conçu pour être lancé par cron sur le VPS, depuis la racine du projet (là où se trouvent
# `.env` et `docker-compose.yml`) :
#
#   0 3 * * * cd /opt/oddscollector && ./scripts/backup_db.sh >> /var/log/oddscollector-backup.log 2>&1
#
# Récupération manuelle (mensuelle ou hebdomadaire, recommandé) vers la machine locale de
# l'utilisateur, depuis un poste ayant accès SSH au VPS :
#
#   scp <utilisateur>@<vps>:/opt/oddscollector/backups/odds_YYYY-MM-DD_HHMMSS.sql.gz .
#
# Ne fait jamais partie de l'image Docker elle-même : c'est un script d'exploitation, pas du code
# applicatif, et il tourne sur l'hôte (ou dans un conteneur cron séparé), jamais dans le
# conteneur `scraper`.
#
# Restauration : toujours via scripts/restore_db.sh, jamais un `psql < fichier.sql` direct — la
# base utilise des hypertables TimescaleDB, qui ont besoin d'un traitement spécial autour de la
# restauration (voir restore_db.sh), sans quoi la restauration échoue avec des erreurs de
# contrainte de clé étrangère (vérifié en conditions réelles, étape 10).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# Charge .env pour connaître POSTGRES_USER / POSTGRES_DB (jamais affichés ni journalisés ici).
# `.env` peut avoir été copié depuis une machine Windows (fins de ligne CRLF) : un `\r` de fin de
# ligne resterait invisible mais corromprait chaque valeur (ex. un rôle "collector" avec un `\r`
# caché, rejeté par PostgreSQL comme un rôle inexistant). Défaut réel trouvé lors du premier
# déploiement VPS (étape 11) — normalisé ici plutôt que de supposer un fichier toujours propre.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source <(tr -d '\r' < .env)
    set +a
fi
: "${POSTGRES_USER:?POSTGRES_USER manquant (voir .env)}"
: "${POSTGRES_DB:?POSTGRES_DB manquant (voir .env)}"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"  # rétention locale sur le VPS ; la rétention des
                                                # données elles-mêmes en base reste indéfinie
                                                # (voir Memoire.md, section 15) — ceci ne concerne
                                                # que les fichiers de sauvegarde compressés.
mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y-%m-%d_%H%M%S)"
dest="$BACKUP_DIR/odds_${timestamp}.sql.gz"
tmp="${dest}.tmp"

echo "[$(date -u +%FT%TZ)] démarrage de la sauvegarde -> $dest"

docker compose exec -T db pg_dump -U "$POSTGRES_USER" --format=plain "$POSTGRES_DB" \
    | gzip -9 > "$tmp"

# Renommage atomique : jamais de fichier .sql.gz tronqué visible en cas d'échec en cours de route.
mv "$tmp" "$dest"
size="$(du -h "$dest" | cut -f1)"
echo "[$(date -u +%FT%TZ)] sauvegarde terminée ($size)"

# Purge des sauvegardes locales plus anciennes que la rétention configurée.
find "$BACKUP_DIR" -name 'odds_*.sql.gz' -mtime "+${RETENTION_DAYS}" -print -delete
