# OddsCollector — collecteur de cotes MelBet Cameroun (esport virtuel)

Collecteur 24/7, en autorisation contractuelle, des matchs, cotes (historique complet, jamais
écrasé), marchés et résultats des ligues virtuelles **Mortal Kombat X** (`1252965`) et
**Mortal Kombat 3** (`2282406`) sur `melbet-cm.com`, pour alimenter des systèmes de prévision et
de suivi en temps réel.

Pour l'historique complet des décisions, de la reconnaissance du site et de chaque étape
d'implémentation (avec les vrais défauts trouvés et corrigés), voir **[Memoire.md](Memoire.md)**.
Pour l'architecture validée (endpoints, schéma, résilience, décisions D1-D9), voir
**[docs/architecture.md](docs/architecture.md)**.

## Principes non négociables

- **Identification honnête, jamais d'évasion anti-bot.** Le collecteur s'identifie toujours avec
  le même `User-Agent` (`OddsCollector/1.0`), ne rotate jamais d'identité, ne simule pas de
  comportement humain, ne résout pas de CAPTCHA. Un blocage (403/429) est un signal d'arrêt et
  d'alerte, jamais quelque chose à contourner. Voir Memoire.md, section 20.
- **Aucun secret dans le dépôt.** Tout ce qui est sensible (mots de passe SMTP, jeton Telegram,
  mot de passe PostgreSQL, mot de passe admin Grafana) vit uniquement dans `.env` (ignoré par
  git). `.env.example` liste les variables attendues, sans valeurs.
- **Rétention indéfinie des données de cotes** : `odds_snapshots` n'est jamais purgé
  automatiquement (une politique de compression pourra être ajoutée plus tard, jamais de
  suppression).

## Démarrage rapide

```bash
cp .env.example .env
# éditer .env : renseigner EMAIL_*, TELEGRAM_*, POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD
docker compose up -d db scraper prometheus grafana
```

Au premier démarrage, le collecteur applique automatiquement les migrations de base de données
(voir `src/collector/storage/migrate.py`), puis commence à sonder les deux ligues toutes les 5
secondes (`config/settings.yaml`).

- Métriques Prometheus du collecteur : accessibles uniquement depuis le réseau Docker
  (`http://scraper:9100/metrics`), scrapées automatiquement par le service `prometheus`.
- Tableau de bord Grafana : http://127.0.0.1:3000 (identifiant `admin`, mot de passe
  `GRAFANA_ADMIN_PASSWORD` défini dans `.env`). Sur un VPS, s'y connecter via un tunnel SSH
  (`ssh -L 3000:localhost:3000 <utilisateur>@<vps>`) — Grafana n'est jamais exposé publiquement.
- Arrêt propre : `docker compose stop scraper` (jusqu'à 90 s pour laisser un rattrapage en cours
  se terminer proprement, voir `stop_grace_period` dans `docker-compose.yml`).

## Ajouter une ligue

Ajouter une entrée dans `config/leagues.yaml` (identifiant, nom, sport) — aucun changement de
code nécessaire.

## Tests

```bash
docker compose run --rm tests                                          # suite complète
docker compose run --rm tests pytest --cov=collector --cov-report=term-missing -q  # avec couverture
```

198 tests (unitaires et intégration), 96 % de couverture (branches). Les tests d'intégration
utilisent une vraie base TimescaleDB (service `db`) ; aucun test ne fait de vraie requête réseau
vers le site (`httpx.MockTransport` partout, données réelles capturées dans `tests/fixtures/`).

## Surveillance externe

`scripts/watchdog.py` vérifie, indépendamment du collecteur lui-même, que chaque ligue a bien
écrit dans `collection_log` récemment (détecte aussi bien un cycle en échec silencieux qu'un
conteneur entièrement à l'arrêt). À installer en tâche planifiée sur le VPS :

```cron
*/5 * * * * cd /opt/oddscollector && docker compose --profile test run --rm tests python scripts/watchdog.py >> /var/log/oddscollector-watchdog.log 2>&1
```

(Le service `tests` est réutilisé tel quel : il partage le réseau Docker vers `db` et a toutes les
dépendances nécessaires, sans dupliquer d'image dédiée pour un script de quelques lignes lancé
toutes les 5 minutes.)

## Sauvegardes

`scripts/backup_db.sh` fait un `pg_dump` compressé et horodaté de la base, avec rotation
automatique des sauvegardes locales (14 jours par défaut, `BACKUP_RETENTION_DAYS`). À installer en
tâche planifiée quotidienne sur le VPS :

```cron
0 3 * * * cd /opt/oddscollector && ./scripts/backup_db.sh >> /var/log/oddscollector-backup.log 2>&1
```

Restauration (reprise après sinistre, ou vérification périodique qu'une sauvegarde est
exploitable) : `scripts/restore_db.sh backups/odds_<date>.sql.gz`. Une récupération régulière
(hebdomadaire recommandé) des fichiers de sauvegarde vers une machine hors VPS est recommandée en
plus des sauvegardes locales elles-mêmes.

## Structure du projet

```
src/collector/          code du collecteur (voir docstrings de chaque module)
  sources/v3/            source principale (API v3 du site)
  sources/legacy/        source de secours (API historique, bascule automatique)
  storage/               requêtes SQL, écriture, migrations
  transport/             client HTTP, limiteur de débit, coupe-circuit, ProxyPool (désactivé)
  observability/         métriques Prometheus, journaux JSON structurés
config/                  configuration non sensible (ligues, paramètres)
migrations/              schéma SQL, appliqué automatiquement au démarrage
scripts/                 outils d'exploitation (watchdog, sauvegarde, restauration)
monitoring/              configuration Prometheus et provisioning Grafana
tests/                   tests unitaires et d'intégration, données réelles de référence
docs/architecture.md     architecture validée et décisions
Memoire.md               journal complet du projet (reconnaissance + implémentation)
```

## Statut

Étapes 3 à 9 terminées et testées (voir Memoire.md pour le détail). Étape 10 en cours
(endurcissement du déploiement). Étape 11 (déploiement VPS réel) en attente de l'accès au VPS.
