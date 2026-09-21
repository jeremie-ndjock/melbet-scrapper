# Architecture proposée : collecteur de cotes Mortal Kombat X et Mortal Kombat 3

Statut : **validée par l'utilisateur le 2026-09-21 (décisions D1 à D9)**. Implémentation en cours, par étapes (voir §14). Base factuelle : [../Memoire.md](../Memoire.md) (reconnaissance, mesures du 2026-09-21).
Les chiffres viennent de deux enregistrements de 30 min (un par ligue) et de la reconnaissance avec navigateur réel. Tout ce qui est une hypothèse est signalé.

---

## 1. Objectif et contraintes

| Sujet | Décision déjà prise (par l'utilisateur) |
|---|---|
| Ligues | Mortal Kombat X (`1252965`) et Mortal Kombat 3 (`2282406`) |
| Pas de temps | 5 s, pour le forecasting |
| Stockage | Option A : une ligne seulement quand une cote, un verrou ou la présence d'une sélection change |
| Rétention | Indéfinie (aucune suppression) |
| VPS | 2 vCPU, 8 Go de RAM, 50 Go NVMe, Pays-Bas (accès pas encore disponible) |
| Alertes | Telegram (`@MortaleKombatbot`) et e-mail (SMTP Mailtrap), configurés dans `.env` |
| Sauvegardes | Transferts mensuels vers la machine locale de l'utilisateur |

Contrainte de production : fonctionner 24 h sur 24, reprendre seul après un redémarrage, ne jamais perdre silencieusement une donnée.

## 2. Ce que la reconnaissance impose à l'architecture

Ces faits mesurés dictent les choix ci-dessous.

1. **Aucune protection anti-bot observée** (pas de challenge, 403 ou 429 sur environ 3 500 requêtes). Le collecteur n'a besoin ni de navigateur ni de token. La couche `AntiBotManager` demandée existe mais reste mince.
2. **Le site interroge son API toutes les 5,2 s, sans WebSocket ni SSE.** Notre polling à 5 s fait comme le site.
3. **Une seule requête `gamesByChamp` par ligue et par cycle** renvoie tous les matchs (3 à 4), leurs scores, leur chrono et toutes leurs cotes. La charge totale est de 0,4 requête par seconde pour deux ligues.
4. **Les cotes ne changent qu'à la fin d'un round** : 3 % (Mortal Kombat X) à 6 % (Mortal Kombat 3) des transitions de 5 s. D'où l'option A, qui écrit environ 5 % des lignes d'une grille complète.
5. **Les marchés changent d'une ligue à l'autre** et au fil d'un match (sélections qui apparaissent, disparaissent, se verrouillent). Le modèle de données ne doit pas coder les marchés en dur.
6. **La fin d'un match est courte** : 15 à 20 s de visibilité pour Mortal Kombat 3, 55 à 70 s pour Mortal Kombat X.
7. **Le serveur est strict sur les paramètres** (ordre alphabétique, dates alignées sur 300 s pour les résultats, `champId` au singulier).
8. **L'historique des résultats existe** (au moins 90 jours, 2 jours par requête) mais pas celui des cotes : les cotes ne peuvent être collectées qu'en direct.

## 3. Sources de données

| Rôle | Endpoint | Fréquence |
|---|---|---|
| **Source principale** (matchs, scores, cotes) | `v3/gamesByChamp` (1 requête par ligue) | toutes les 5 s |
| Tableau des rounds (durée, vainqueur, type de finish) | `v3/statistic` par match | uniquement quand le score change (environ 8 fois par match) |
| Résultats officiels, rattrapage de l'historique | `result/web/api/v3/games` | toutes les 5 min (réconciliation), et rattrapage de 90 jours au premier lancement |
| Libellés des marchés | fichiers du CDN (`bets_model_map_full_fr.json` et chunks) | au démarrage, puis toutes les 6 h |
| **Source de secours** | legacy `GetChampZip` + `GetGameZip` | activée automatiquement si la source principale est en erreur de structure |

Deux sources indépendantes pour les cotes : un changement de structure d'un côté ne coupe pas la collecte.

## 4. Vue d'ensemble

```text
                    ┌──────────────────────────────────────────────┐
                    │ Processus unique Python (asyncio)            │
  config/leagues    │                                              │
  ────────────►  Scheduler (une boucle par ligue, cycle de 5 s)    │
                    │      │                                       │
                    │      ▼                                       │
                    │  Transport (httpx, connexions persistantes,  │
                    │  RateLimiter, Retry+jitter, CircuitBreaker)  │
                    │      │  ◄── AntiBotManager (mince, config)   │
                    │      ▼                                       │
                    │  Parser (pydantic v2, validation de schéma)  │──► Dead letter (payload brut)
                    │      │ ParserError → alerte + source dégradée│
                    │      ▼                                       │
                    │  Normalizer (identité de sélection,          │
                    │  libellés du dictionnaire, ronde/ligne)      │
                    │      ▼                                       │
                    │  ChangeDetector (dernier état en mémoire)    │
                    │      ▼                                       │
                    │  Storage queue (bornée) ──► Writer (asyncpg, │
                    │                             lots, idempotent)│
                    │                                              │
   ResultCollector · DictionaryLoader · Watchdog/Alerting · Metrics │
                    └───────────────┬──────────────────────────────┘
                                    ▼
              PostgreSQL 16 + TimescaleDB          Prometheus ◄─ /metrics
                                                        │
                                                    Grafana (localhost)
```

Règle de séparation stricte : transport, authentification, découverte, parsing, normalisation, stockage sont des modules distincts qui ne s'appellent que par des interfaces (types pydantic).

## 5. Modules et responsabilités

| Module | Rôle | Points d'attention issus des mesures |
|---|---|---|
| `transport` | Client HTTP asynchrone, limiteur de débit, reprises, coupe-circuit | Délai d'attente 10 s (99e centile mesuré : 2 s). `204` = « plus de données », pas une erreur. Paramètres triés par ordre alphabétique. |
| `antibot` | `SessionManager` (cookies), `RateLimitManager`, `RetryManager`, `BrowserProfileManager` (User-Agent), `ProxyPool` | Composants minces et **configurables**. `ProxyPool` complet mais **désactivé** : il devient utile seulement si l'IP néerlandaise est bloquée. `ChallengeManager` : interface vide (aucun défi observé). **Aucune évasion de détection anti-bot n'est implémentée** (pas de rotation d'identité, pas d'imitation de navigateur ou de TLS, pas de résolution de CAPTCHA, pas de contournement de blocage) — décision explicite de l'utilisateur, voir Memoire.md section 20. |
| `sources.v3`, `sources.legacy` | Appels et modèles de réponse | Le parseur ne suppose aucun groupe précis : il accepte les groupes inconnus. |
| `parsing` | Validation de schéma (pydantic) | Un `ParserError` marque l'endpoint « dégradé », enregistre le payload brut (dead letter), alerte, et bascule sur la source de secours. |
| `normalize` | Sélection = `(groupe, type, paramètre)` ; décodage de `eventParams` (round, ligne) ; libellés | Pour la source legacy, le paramètre est décodé (`round x 100 + ligne / 100` pour la durée du round). |
| `dedupe` | Détecte les changements de `(cote, verrou)` et l'apparition ou la disparition de sélections | `updateTs` change presque à chaque réponse : **il ne sert pas à détecter un changement**, on compare le contenu. |
| `storage` | Écriture en lots avec `INSERT … ON CONFLICT DO NOTHING`, migrations SQL | Idempotent : rejouer un lot ne crée pas de doublon. |
| `results` | Résultats officiels, rattrapage, réconciliation des matchs terminés | Parseur tolérant : score à suffixe Mercy, codes de finish à 1 ou 2 lettres, codes inconnus. |
| `dictionary` | Charge et rafraîchit les libellés | Groupe inconnu du dictionnaire : enregistré et alerté, sans arrêt. |
| `alerting` | Telegram + e-mail, déduplication, limitation | Une alerte identique au plus toutes les 30 min, résumé quotidien. Quota réduit du domaine de démonstration Mailtrap. |
| `observability` | Journaux JSON, métriques Prometheus, `/health` | Secrets masqués dans tous les journaux. |

## 6. Modèle de données (PostgreSQL 16 + TimescaleDB)

**Choix de la base.** PostgreSQL avec TimescaleDB, en un seul conteneur. Le volume est modeste (voir §7), mais on garde les données pour toujours : la compression native de Timescale et les tables partitionnées par le temps évitent un changement de base plus tard, sans ajouter de composant (c'est une extension). ClickHouse serait disproportionné sur 2 vCPU ; SQLite reste pour les tests locaux rapides uniquement.

Tables (résumé, DDL complet à l'étape 3) :

```text
leagues           (league_id PK, name, sport_id)
events            (game_id PK, league_id, feed_id, match_no, p1_id, p2_id, p1_name, p2_name,
                   start_ts, status[scheduled|live|finished|vanished], first_seen, last_seen)
markets_dict      (group_id, type_id, group_label_fr, type_label_fr, updated_at)  PK (group_id, type_id)

odds_snapshots    -- hypertable, partition sur ts_server, intervalle de 7 jours
                  (ts_server timestamptz, collected_at timestamptz, league_id, game_id,
                   g int, t int, param numeric(12,4) NOT NULL DEFAULT 0,   -- 0 = sans paramètre
                   odds numeric(8,3) NULL,            -- NULL = sélection retirée
                   blocked bool, is_center bool, round_no smallint NULL, line numeric(6,2) NULL,
                   source smallint, latency_ms int)
                  PK (ts_server, game_id, g, t, param)

game_state        (ts_server, game_id, period, score1, score2, elapsed_s, status_text)   -- sur changement
round_results     (game_id, round_no, winner, seconds, finish_di, finish_code, wt, fw,
                   mercy_p1, mercy_p2)   PK (game_id, round_no)
results           (game_id PK, league_id, final_score1, final_score2, winner, raw_score_string,
                   date_start, source, captured_at)
collection_log    -- hypertable : (ts, league_id, source, endpoint, ok, http_status, latency_ms,
                                    n_games, n_rows_written)
dead_letter       (id, ts, source, endpoint, error, payload jsonb)     -- payloads en échec de parsing
checkpoints       (worker, key, value, updated_at)
schema_migrations (version, applied_at)
```

Points de conception :

- **Ne rien remplacer silencieusement** : `odds_snapshots` est en ajout seul ; l'état courant se déduit de la dernière ligne par sélection.
- **Une sélection retirée est une ligne** avec `odds = NULL` : c'est une information (ligne retirée par le bookmaker), et elle évite de confondre « absent » et « inchangé ». Coût mesuré : environ +25 à +35 % de lignes.
- **`collection_log` est indispensable avec l'option A.** Sans lui, on ne peut pas distinguer « la cote n'a pas changé » de « le collecteur était aveugle ». Toute reconstruction d'une grille de 5 s ne reporte une valeur que sur les intervalles couverts par un cycle réussi.
- **Compression** Timescale au-delà de 7 jours (`segmentby = league_id, g, t`, `orderby = game_id, param, ts_server`), à ajuster après mesure. **Aucune politique de suppression.**
- **Index** : la clé primaire ci-dessus ; `(game_id, ts_server)` pour relire un match ; `(league_id, ts_server)` pour les extractions par ligue.
- **Migrations** : fichiers SQL numérotés dans `migrations/`, appliqués par un petit exécuteur (pas d'ORM ni d'Alembic : une dépendance de moins).
- **Grille de 5 s pour le forecasting** : une fonction SQL et un script `scripts/export_grid.py` (`time_bucket_gapfill` avec report de la dernière valeur, limité aux intervalles couverts par `collection_log`), exportable en CSV ou Parquet.

## 7. Volumes et dimensionnement

| Élément | Mesure ou estimation |
|---|---|
| Lignes `odds_snapshots` (changements) | Mortal Kombat X : environ 306 par match ; Mortal Kombat 3 : environ 444. Environ **216 000 par jour** pour les deux ligues, **+ 25 à 35 %** avec les retraits, soit environ **280 000 par jour** |
| Lignes `collection_log` | environ 35 000 par jour |
| Taille brute | environ 30 Mo par jour (hypothèse : 100 octets par ligne), environ 11 Go par an brut, **environ 1,5 Go par an compressé** (hypothèse : compression x8) |
| Disque | 50 Go suffisent pour plus de 10 ans compressé, à surveiller (alerte à 80 %) |
| Réseau | 0,4 à 0,5 requête par seconde. Réponse `gamesByChamp` : 16,7 Ko décompressés en moyenne pour Mortal Kombat X (mesuré), plus pour Mortal Kombat 3 (50 sélections ; hypothèse : environ 27 Ko). Soit environ 0,8 Go par jour décompressés et environ 100 Mo par jour sur le fil (compression HTTP d'un facteur environ 8, estimation). Négligeable face à 4 To |
| Mémoire et processeur | Un seul processus Python léger (< 300 Mo) ; PostgreSQL avec `shared_buffers` d'environ 1 Go ; Prometheus et Grafana d'environ 300 à 500 Mo. Marge large sur 8 Go |

## 8. Résilience

| Panne | Comportement |
|---|---|
| Timeout, DNS, connexion coupée | Reprise au cycle suivant ; réessai avec attente exponentielle et jitter ; **jamais de boucle serrée** |
| HTTP 429 ou 403 | Ralentissement automatique du limiteur, alerte si le taux augmente ; activation possible du `ProxyPool` (désactivé par défaut) |
| HTTP 5xx | Coupe-circuit par endpoint (ouvert après N échecs, test périodique de fermeture) |
| JSON invalide ou structure changée | `ParserError` → payload en `dead_letter` → endpoint « dégradé » → alerte → bascule sur la source de secours ; **la collecte des autres sources continue** |
| Groupe de marché inconnu | Stocké tel quel `(g, t, param)`, alerte, aucun arrêt |
| Base indisponible | La file de stockage (bornée) se remplit puis applique une contre-pression ; alerte immédiate ; reprise et vidage à la reconnexion ; en dernier recours, spool sur disque |
| Redémarrage du VPS ou du conteneur | `restart: unless-stopped` ; au démarrage, le dernier état de chaque sélection est relu depuis la base (pas de faux « changements » massifs) ; les checkpoints reprennent le rattrapage des résultats |
| Arrêt propre | Les lots en attente sont écrits avant la sortie |

## 9. Observabilité et alertes

**Métriques** (Prometheus) : `requests_total`, `requests_success`, `requests_failed`, `response_latency`, `rate_limit_events`, `authentication_failures`, `parser_errors`, `events_collected`, `odds_collected`, `duplicates`, `proxy_failures`, `queue_depth`, `database_latency`, plus `games_live`, `last_odds_change_age_seconds`, `disk_used_ratio`, `last_backup_age_seconds`.

**Journaux** : JSON structuré (`timestamp`, `level`, `component`, `league_id`, `game_id`, `latency_ms`, `status_code`), secrets masqués.

**Alertes** (Telegram et e-mail, limitées et dédupliquées) :

| Condition | Seuil proposé |
|---|---|
| Taux d'erreur élevé | plus de 20 % de requêtes en échec sur 5 min |
| Aucun match reçu | 0 match pendant plus de 2 min (il y en a toujours 3 à 4 par ligue) |
| Cotes figées | aucun changement de cote sur une ligue pendant plus de 10 min (les cotes bougent à chaque round, environ toutes les 40 s) |
| Changement de structure | tout `ParserError`, ou groupe inconnu |
| 403 ou 429 en hausse | plus de 3 sur 5 min |
| Base indisponible | échec d'écriture ou de connexion |
| Disque presque plein | plus de 80 % |
| Latence élevée | 95e centile au-dessus de 5 s pendant 5 min |
| Sauvegarde absente | dernière sauvegarde de plus de 36 h |

**Surveillance du surveillant** : si le collecteur lui-même tombe, aucune alerte interne ne part. Un script `scripts/watchdog.sh`, lancé toutes les 5 min par `cron` **hors du conteneur**, vérifie `/health` et l'âge de la dernière ligne écrite, et envoie un message Telegram si besoin. Pas d'Alertmanager : un composant de moins.

## 10. Déploiement (Ubuntu 24.04)

Services Docker Compose : `scraper`, `db` (image TimescaleDB PostgreSQL 16), `prometheus`, `grafana`. **Pas de Redis** : les files `asyncio` et les checkpoints en base suffisent.

- **Sécurité** : conteneur du collecteur non root ; PostgreSQL, Prometheus non publiés (réseau Docker interne) ; Grafana lié à `127.0.0.1` et consulté par tunnel SSH ; seul le port SSH est ouvert (pare-feu `ufw`), `fail2ban`, mises à jour de sécurité automatiques ; `.env` en droits `600`, jamais dans une image.
- **Persistance** : volumes nommés pour la base, Prometheus et Grafana ; `restart: unless-stopped` ; fuseau horaire UTC.
- **Sauvegardes** : `pg_dump` compressé **quotidien sur le VPS** (7 quotidiennes + 4 hebdomadaires), script de récupération `scripts/pull_backup` pour votre machine (mensuel comme décidé, hebdomadaire recommandé), script de restauration testé.
- **Environnement de développement** : votre machine a Python 3.14 et Docker. Je propose de développer et tester **dans Docker avec Python 3.12** pour éviter les incompatibilités de bibliothèques et garantir que ce qui passe en local passe sur le VPS.

## 11. Dépendances (volontairement minimales)

`httpx[http2]`, `pydantic` v2, `asyncpg`, `prometheus-client`, `PyYAML`. Journalisation, reprises, coupe-circuit, migrations et e-mail : bibliothèque standard ou code maison de quelques dizaines de lignes. Tests : `pytest`, `pytest-asyncio`, `respx` (simulation HTTP). Pas de Playwright en production.

## 12. Organisation du projet

```text
src/collector/{config,transport,antibot,sources,parsing,normalize,dedupe,storage,results,dictionary,alerting,observability,scheduler}/
config/            leagues.yaml, settings.yaml
migrations/        001_init.sql, 002_..., ...
tests/             unit/, integration/, failure/, fixtures/
monitoring/        prometheus.yml, provisioning et tableaux de bord Grafana
scripts/           backup.sh, restore.sh, pull_backup.*, watchdog.sh, export_grid.py
docs/              architecture.md, runbook.md, endpoints.md
Dockerfile · docker-compose.yml · .env.example · requirements.txt · Makefile · README.md
```

Les ligues sont définies dans `config/leagues.yaml` : ajouter une ligue est un changement de configuration, pas de code.

## 13. Tests

- **Unitaires** : parseurs (v3, legacy, résultats), décodage des paramètres, détection de changement, déduplication, validation.
- **Intégration** : base réelle (conteneur), écriture idempotente, reprise après redémarrage, migrations, files, `ProxyPool`, `SessionManager`.
- **Pannes simulées** : 429, 403, 500, timeout, JSON corrompu, proxy mort, changement de schéma, groupe inconnu, base coupée.
- **Jeux de données réels** : les deux enregistrements de 30 min (7,4 Mo et 8,5 Mo) servent de base. Je propose de copier dans `tests/fixtures/` un extrait réduit : un match complet par ligue et les cas de fin de match.

## 14. Plan de réalisation

| Étape | Livrable | Critère de réussite |
|---|---|---|
| 3. Schéma **(terminée)** | Migrations SQL, conteneur de base, Timescale configuré | Migrations rejouables ; tests de contraintes et d'idempotence : **25 tests passés** (voir Memoire.md, section 20) |
| 4. Prototype **(terminée)** | Source v3, parseur, normaliseur, détecteur de changement, écriture pour une ligue | Un match complet stocké, identique à l'enregistrement de référence : **vérifié** (62 tests, voir Memoire.md section 21) |
| 5. Normalisation et stockage **(terminée)** | Dictionnaire, résultats, rattrapage de l'historique | 45 tests, dont un rattrapage qui reprend correctement après une panne simulée en plein milieu (voir Memoire.md, journal d'étape) |
| 6. Concurrence **(terminée)** | Deux ligues, file bornée, checkpoints, reprise, ordonnanceur permanent | Redémarrage sans doublon ni trou : **vérifié** (117 tests, plus un essai réel ; voir Memoire.md, journal d'étape) |
| 7. Résilience **(terminée)** | Transport complet, `ProxyPool`, coupe-circuit, source de secours avec bascule automatique | Tous les scénarios de panne pertinents pour ce site passent (140 tests, plus une vérification réelle de la source de secours ; voir Memoire.md, journal d'étape) |
| 8. Observabilité | Métriques, tableaux de bord, alertes, watchdog | Alertes reçues sur Telegram et e-mail lors d'une panne simulée |
| 9. Tests | Couverture unitaire, intégration, pannes | Suite complète verte |
| 10. Docker | Images, Compose, sauvegardes | `docker compose up` de zéro fonctionne |
| 11. VPS | Runbook, déploiement, **test géographique depuis l'IP néerlandaise** | Collecte stable 24 h |

À chaque étape : fichiers créés, code complet, commandes d'installation, tests à exécuter, résultats attendus, problèmes possibles.

## 15. Risques restants

1. **IP du VPS** (accès pas encore disponible) : le site pourrait rediriger ou bloquer. Un test multi-pays (adresses de centres de données) a donné HTTP 200 depuis 20 pays sur 21, ce qui réduit le risque sans le supprimer (voir Memoire.md, section 9). À reconfirmer depuis l'IP réelle du VPS avant la mise en production. Plan de repli : `ProxyPool` activé avec un proxy sortant vers une IP acceptée.
2. **Changement du site** : endpoints, paramètres ou dictionnaire. Atténué par la validation de schéma, la source de secours et les alertes.
3. **Domaine de démonstration Mailtrap** : envoi limité au titulaire du compte, quota réduit. Atténué par la limitation des alertes ; domaine propre recommandé à terme.
4. **Sauvegardes mensuelles** : perte possible de 30 jours de cotes irremplaçables si le VPS disparaît. Atténué par les sauvegardes quotidiennes locales ; récupération hebdomadaire recommandée.
5. **Nature des cotes** : matchs virtuels ; les cotes sont vraisemblablement générées par un modèle. À garder à l'esprit pour le forecasting.
6. **Secrets partagés dans la conversation** : régénérer le mot de passe SMTP et révoquer le token Telegram si la conversation est conservée.

## 16. Décisions à valider

| # | Décision | Ma recommandation |
|---|---|---|
| D1 | Source principale v3 (`gamesByChamp`), legacy en secours | Oui |
| D2 | Option A + lignes de retrait (`odds NULL`) + `collection_log` | Oui |
| D3 | PostgreSQL 16 + TimescaleDB en un conteneur, aucune suppression, compression après 7 jours | Oui |
| D4 | Prometheus + Grafana (Grafana en `127.0.0.1`, tunnel SSH) ; alertes dans l'application ; watchdog externe ; **sans** Alertmanager | Oui |
| D5 | `ProxyPool` réalisé mais désactivé par défaut (utile si l'IP néerlandaise est bloquée) | Oui |
| D6 | Développement et tests dans Docker avec Python 3.12 | Oui |
| D7 | Sauvegarde quotidienne sur le VPS + récupération par l'utilisateur, **hebdomadaire** plutôt que mensuelle | Oui (validé ; l'utilisateur peut revenir au mensuel) |
| D8 | Copier un extrait des enregistrements dans `tests/fixtures/` | Oui |
| D9 | Aucun Redis, aucun Alertmanager, aucun ORM | Oui |
