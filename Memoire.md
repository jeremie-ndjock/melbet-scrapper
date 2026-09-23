# Reconnaissance MelBet Cameroun – Mortal Kombat X (ligue `1252965`)

Date de la reconnaissance : 2026-09-21
URL cible : https://melbet-cm.com/fr/esports/virtual/mortal-kombat/1252965-mortal-kombat-x

Méthode : environ 50 requêtes légères avec `curl` (phase 1), puis exploration avec un navigateur réel via Playwright MCP et une trentaine de requêtes supplémentaires espacées (phase 2). Rien n'a été codé à ce stade.
Le comportement depuis une IP de VPS et sur la durée n'a pas été testé.

Dernière mise à jour : 2026-09-21 (alertes testées ; architecture proposée dans docs/architecture.md ; voir sections 13, 18 et 19).

---

## 1. Architecture détectée

- Front SSR sur une plateforme « v3 » en micro-frontends. Une app hôte (`sys-v3-host-app`) charge une app esports (`__CYBER_APP__`).
- Passerelle Envoy, sans Cloudflare ni Akamai visible. CDN de statiques : `v3.cdnafric.com`.
- Le SSR embarque les 4 matchs de la ligue (IDs, équipes) dans `window.__V3_HOST_APP__`. Les marchés et cotes ne sont **pas** rendus côté serveur (`_errors` sur `cyber-markets-*`) : ils arrivent par API après le chargement.
- Matchs **virtuels** : flux simulé (`VI: "xgame7_…"`), pas des rencontres réelles.
- Bundles analysés :
  - hôte : `/sys-static/sys-v3-host-app-static/Desktop/Melbet/entry-703548f048.js`
  - cyber : `/sys-static/sys-cyber-app-static/Desktop/Melbet/entry-1481664f7c.js` (69 chunks lazy non scannés)

## 2. Endpoints / API identifiés

**Correction importante (phase 2).** En phase 1, j'avais supposé `country=40` et `partner=0`. Les vraies valeurs envoyées par le site, relevées dans le navigateur, sont : `fcountry=84` (ou `country=84`, le Cameroun), `ref=8` (ou `partner=8`) et `gr=2147`. L'API legacy accepte encore les anciennes valeurs, mais on utilisera les vraies.

Aucun de ces endpoints ne demande de cookie, de token ni de header spécial.

### 2.1 Endpoints utilisés par le site (v3), à privilégier

Règle observée : les paramètres doivent être passés **dans l'ordre alphabétique**, sinon le serveur répond 400 `InvalidQueryParametersException` (constaté sur `gameEvents`, `statistic` et `gameInfo`).

| Endpoint | Statut | Contenu |
|---|---|---|
| `GET /cyber-api/mainfeedlive/web/cyber/v3/gamesByChamp?cfView=3&champId=1252965&fcountry=84&gr=2147&lng=fr&ref=8` | 200 | **Tous les matchs de la ligue en une requête** : équipes, `scores` (score, round, chrono), `updateTs`, et `eventGroups` avec toutes les cotes. Sert de découverte et de collecte. |
| `GET .../v3/gameEvents?cfView=3&fcountry=84&gameId={id}&gr=2147&lng=fr&ref=8` | 200 | Cotes d'un match : `groupId`, `type`, `parameter`, `cf`, `blocked`, `isCenter`, `eventParams.params`. Réponse `cache-control: no-cache,no-store` (pas de cache serveur). |
| `GET .../v3/statistic?fcountry=84&gameId={id}&gr=2147&lng=fr&ref=8` | 200 | Score, round, chrono (`timer.timeSec`), tableau des rounds (`statistic.main.RoundTable`). |
| `GET .../v3/gameInfo?fcountry=84&gameId={id}&gr=2147&lng=fr&ref=8` | 200 | Fiche du match : ligue, participants et leurs IDs, flux vidéo, `startTs`, `updateTs`. |
| `GET .../v3/leftmenu/virtual?fcountry=84&gr=2147&lng=fr&ref=8[&champIds=1252965&sportIds=103]` | 200 | Menu des sports et ligues virtuels (découverte d'autres ligues). |

### 2.2 Résultats et historique

| Endpoint | Statut | Contenu |
|---|---|---|
| `GET /service-api/result/web/api/v3/games?champId=1252965&dateFrom={ts}&dateTo={ts}&lng=fr&ref=8&sportIds=103` | 200 | Matchs terminés de la ligue : `id`, participants et leurs IDs, `score` détaillé par round, `dateStart`, `videos`. |
| `GET /service-api/result/web/api/v2/sports?cyberFlag=4&dateFrom={ts}&dateTo={ts}&gr=2147&lng=fr&ref=8` | 200 | Liste des sports ayant des résultats. |
| `GET /service-api/result/web/api/v2/champs?dateFrom={ts}&dateTo={ts}&lng=fr&ref=8&sportIds=103` | 200 | Ligues du sport 103 avec `gamesCount`. |

Règles observées pour le service de résultats :
- `champId` au **singulier** (`champIds` donne 400).
- `dateFrom` et `dateTo` doivent être des **multiples de 300 s** (fenêtres de 5 min). Sinon 400.
- Une requête couvre au plus **2 jours** (3 jours ou plus : 400).
- L'historique remonte **au moins à 90 jours** : des fenêtres de 1 jour décalées de 3, 7, 30 et 90 jours renvoient chacune 289 matchs. La limite maximale d'ancienneté n'a pas été cherchée.
- Pas de pagination : la réponse contient tous les matchs de la fenêtre (`count` = nombre d'éléments, 285 à 289 par jour).
- Format du score : `5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)` = score final, puis pour chaque round « point au participant 1 ou 2 » et le type de finish : **R** = Regular, **F** = Fatality, **B** = Brutality (occurrences observées sur 285 matchs : 1206 R, 584 F, 294 B).
- **Codes de finish des résultats (corrigé après l'enregistrement de Mortal Kombat 3)** : un code peut comporter **une ou deux lettres** ; un décompte précédent sur une seule lettre tronquait `Ba` et `Fr`. Codes : `R` Regular, `F` Fatality, `B` Brutality, `Ba` Babality, `Fr` Friendship (Amitié), `An` Animality, `Hk` Hara-Kiri. Confirmés par rapprochement direct avec le tableau des rounds : R, F, B (37 rounds), Ba et Fr ; `An` et `Hk` sont déduits des libellés du dictionnaire. Mortal Kombat X n'utilise que R, F, B.
- **Format de score de Mortal Kombat 3** : chaque round porte un suffixe, ex. `5:3(0:1 F ,M- / M-; 1:0 Ba ,M- / M-; ...)`. `M+` / `M-` sont les drapeaux **Mercy** du participant 1 / 2 (sur 24 h : `M- / M-` 1 996 fois, `M- / M+` 63 fois, `M+ / M-` 42 fois, soit 5,0 % des rounds avec un Mercy ; jamais les deux). Le parseur doit tolérer ce suffixe, les codes à une ou deux lettres et les codes inconnus.

### 2.3 Dictionnaire des libellés de marchés (fichiers statiques du CDN)

| Endpoint | Statut | Contenu |
|---|---|---|
| `GET https://v3.cdnafric.com/genfiles/cms/betstemplates/bets_model_map_full_fr.json` | 200 | Table `chunk -> [groupId min, groupId max]`. Le chunk 0 est utilisé pour les groupes de base (1x2, Total, Total 1, Total 2). |
| `GET https://v3.cdnafric.com/genfiles/cms/betstemplates/bets_model_full_fr_{chunk}.json` | 200 | Libellés : `{ "<chunk>": { "<groupId>": { "N": nom du groupe, "M": { "<type>": { "N": libellé de la sélection } } } } }`. |

### 2.4 Endpoints legacy (conservés comme source de secours)

| Endpoint | Statut | Contenu |
|---|---|---|
| `GET /service-api/LiveFeed/GetChampZip?champ=1252965&lng=fr&country=84&partner=8&virtualSports=true&gr=0` | 200 | Matchs de la ligue (`I`, `O1`/`O2`, `S`, `SC`, `EC`). Pas de cotes. Réponse `cache-control: public,max-age=5`. |
| `GET /service-api/LiveFeed/GetGameZip?id={I}&lng=fr&isSubGames=true&GroupEvents=true&countevents=250&grMode=4&country=84&partner=8` | 200 | Détail d'un match : `SC`, `GE[].E[][]` avec `{G,T,P,C,B,CE}`. `B` = bloqué. |
| `GET /service-api/LineFeed/GetChampZip…` | 200, `Value:null` | Pas de pré-match pour cette ligue. |
| `/service-api/LiveFeed/Get1x2_VZip` | 200 côté site, 406 avec mes paramètres | Non utilisé pour la collecte. Le site l'appelle avec une liste de sports et `getEmpty=true&isRecommendations=true`. |
| `/service-api/statisticfeed/api/v1/Cyber/Game?id=` | 200, corps vide | Sans intérêt. |

Détail commun aux endpoints fonctionnels :

```text
METHOD          GET
HEADERS         Accept: application/json (suffisant). Referer facultatif.
COOKIES         aucun
AUTHENTICATION  aucune
REQUEST BODY    aucun
RESPONSE FORMAT JSON
                legacy : enveloppe {Id, Success, Error, ErrorCode, Value}, match terminé -> Value:null
                v3     : objets directs (liga, games, eventGroups, ...)
PAGINATION      aucune (3 à 4 matchs par ligue ; résultats : fenêtre de 2 jours max)
RATE LIMIT      non observé (voir section 5)
```

### 2.5 Ligues Mortal Kombat existantes (sport 103)

Sept ligues ont des matchs sur les dernières 24 h (`gamesCount` = nombre de matchs) :

| `champId` | Ligue | Matchs sur 24 h |
|---|---|---|
| **1252965** | **Mortal Kombat X (cible actuelle)** | 286 |
| 2068436 | Mortal Kombat 11 | 283 |
| 2622648 | Mortal Kombat 1 | 286 |
| 2282406 | Mortal Kombat 3 | 286 |
| 1961974 | Mortal Kombat 11 (BO3) | 359 |
| 2488812 | Mortal Kombat X. Tournament | 141 |
| 2332171 | Mortal Kombat 11 XXL | 141 |

Le mécanisme est le même pour toutes : un simple paramètre `champId` à changer.

**Ligues retenues par l'utilisateur (2026-09-21) : Mortal Kombat X (`1252965`) et Mortal Kombat 3 (`2282406`).** Mortal Kombat 3 a été vérifié : mêmes endpoints, mêmes règles (premier à 5 rounds, 5 à 9 rounds par match, un match toutes les 5 min avec 3 à 4 visibles), 284 matchs sur 24 h, historique de résultats disponible à 90 jours. Son statut `statistic` et son `RoundTable` ont la même structure. Il propose en revanche des **marchés supplémentaires** (section 15).

Décodage des paramètres du bundle : `country`/`fcountry`, `gameId`, `champ`/`champId`, `sport`/`sportIds`, `lng`, `ref`, `gr` (groupId), `cfview`/`cfView`, `userId`, `virtualSports`, `partner`. Config de la page : `rd=melbet-cm.com`, `rg=CM`, `rp=2147`.

## 3. Flux temps réel

- **Confirmé par un navigateur réel** : aucun WebSocket ni SSE n'a été ouvert pendant 40 s sur la page d'un match en direct (écoute avec `page.on('websocket')` et contrôle des réponses `text/event-stream` : 0 occurrence).
- Le site fait du **polling HTTP** : il appelle `v3/gameEvents` et `v3/statistic` ensemble, puis recommence **toutes les 5,2 s environ** (écarts mesurés : 5,2 à 5,8 s sur 7 cycles).
- Le pas de 5 s retenu pour la collecte correspond donc à la cadence du site.
- Les endpoints v3 ne sont pas mis en cache côté serveur (`no-cache,no-store`), alors que le legacy l'est pendant 5 s.

## 4. Mécanismes de session

- Seul cookie utile : `auid`. Sa valeur est du base64 de `IP|User-Agent`, c'est un identifiant et non un token.
- `platform_type`, `lng`, `tzo`, `is12h`, `cookies_agree_type` sont des préférences.
- Les appels API sans aucun cookie fonctionnent. Le `SessionManager` restera donc une interface mince.

## 5. Mécanismes anti-bot / WAF / rate limit

- Rien d'observé : pas de challenge JS, pas de CAPTCHA, pas de 403, pas de 429. Un `curl` avec un User-Agent standard suffit.
- Header `x-gw-rds` : règles de redirection (probablement géographiques). Non testé depuis une IP de VPS.
- Rafale de 15 requêtes parallèles : 15 réponses 200. Cela ne prouve pas l'absence de limite, seulement qu'elle n'est pas basse.
- Le navigateur Playwright (Chromium) accède à la page sans blocage ni challenge.
- **Enregistrement de 30 min** à environ 1 requête par seconde (1 776 requêtes, cycle de 5 s) : aucun 403, aucun 429, aucun blocage. Latence médiane d'environ 0,75 s, 19 requêtes sur 1 771 au-dessus de 2 s (maximum 4,4 s).
- **Deuxième enregistrement (Mortal Kombat 3, 30 min)** : 1 450 requêtes, aucun 403, aucun 429, aucune erreur réseau. Latences sur les deux enregistrements : médiane environ 730 ms, 95e centile 1,2 à 1,3 s, 99e centile environ 2 s, pics isolés jusqu'à 4,4 s (Mortal Kombat X) et 16,4 s (Mortal Kombat 3). Délai d'attente recommandé pour le collecteur : 10 s, avec reprise au cycle suivant.
- Les erreurs 400 renvoyées sont des erreurs de validation de paramètres (ordre alphabétique, noms, dates alignées), pas des blocages.

## 6. Structure des données

- **Identité** : match `I` (ex. `754862258`), `N` (numéro), `VI` (ID du flux), ligue `LI=1252965`, sport `SI=103`.
- **Participants** : `O1`/`O2` (noms FR/RU/EN), `O1I`/`O2I` (IDs stables des personnages).
- **Horaires** : `S` (début, epoch), `U` (timestamp serveur de la réponse). Avant le début, `TS` est un compte à rebours et `GNS:true`. Pendant le match, `TS` est le temps écoulé en secondes.
- **Score** : `SC.FS` (score en rounds), `SC.CPS` (round courant), `SC.S[RoundTable]` (JSON imbriqué en chaîne : vainqueur, type Regular/Fatality/Brutality, durée).
- **Cotes** : `{G: groupe de marché, T: type de sélection, P: paramètre (ligne/handicap, ex. 800.255), C: cote}`.
- **Libellés de marchés** : absents des réponses de cotes, mais disponibles dans le dictionnaire statique du CDN (section 2.3, décodage en section 15). Résolu.
- **Résultats** : le match disparaît du flux en direct à sa fin (`Value:null` sur un ancien ID), mais il est ensuite disponible dans le service de résultats (section 2.2), avec l'historique. Le dernier état des **cotes** ne l'est pas : il faut le capturer avant la disparition.
- **Règle de fin de match** : le premier participant à **5 rounds gagnés** l'emporte (scores finaux observés 5:0, 5:3, 3:5, 2:5, 4:5), soit 5 à 9 rounds par match.

## 7. Fréquence des mises à jour (mesurée)

Mesure du 2026-09-21, de 12:10 à 12:40 UTC : 361 cycles de 5 s (durée réelle 1 805 s, écart médian entre cycles 5,00 s), 10 matchs vus dont **3 suivis en entier** (de 4,5 min avant le début jusqu'à leur disparition du flux).

- **Cadence de production** : un match démarre toutes les 5 min (écart constant de 300 s sur `S`). Un match reste visible 14 à 19 min (moyenne 1 033 s sur les 3 matchs complets : environ 270 s avant le début, puis 600 à 880 s de jeu). 3 à 4 matchs sont visibles simultanément.
- **Avant le début** : les cotes ne bougent pas du tout (0 changement sur 52 à 54 cycles par match). Ce sont des cotes d'ouverture.
- **Pendant le match, les cotes ne changent qu'à des moments précis**, jamais au milieu d'un round : sur tous les matchs, **40 transitions de 5 s sur 1 344 (3,0 %)** contiennent au moins un changement de cote, et 100 % d'entre elles coïncident avec un changement de score (fin d'un round). Sur les 3 matchs complets : 8, 7 et 4 événements de changement de cote.
- **Cycle observé pour chaque round** (chronologie détaillée du match Quan Chi - Scorpion) :
  1. **Fin du round** (le score change) : toutes les sélections ouvertes (12 à 20) changent de cote en même temps et se **déverrouillent**. C'est la fenêtre de pari (environ 40 s).
  2. **Début du round suivant** (la période affichée passe à N+1, environ 40 s plus tard) : environ 13 sélections propres au round sont **remplacées** (13 apparaissent, 13 disparaissent) et la plupart des sélections sont **verrouillées** jusqu'à la fin du round (environ 60 s).
- **Fin de match** : dès que le score final est atteint, les 17 à 25 sélections sont toutes verrouillées, mais le match reste **listé environ 55 à 70 s** avec le libellé « Jeu terminé » avant de disparaître. Le dernier état est donc capturable à 5 s. Le `statistic` d'un match disparu renvoie **204 sans corps** (constaté 4 fois sur 4 fins de match ; une cinquième fois sur un match pas encore commencé, sans conséquence).
- **Nombre de sélections** : 33 au début d'un match, 17 à 19 à la fin (moyenne 27,3), car le bookmaker retire des lignes au fil du match.
- **`updateTs`** avance presque à chaque relevé (1 337 fois sur 1 344, identique 7 fois). Ce n'est **pas** un indicateur de changement de cote : détecter les changements en comparant les cotes et les verrous, pas en regardant `updateTs`.
- **Cohérence des deux endpoints** : `gamesByChamp` et `gameEvents` donnent les mêmes cotes (56 relevés identiques sur 60). Les 4 écarts tombent sur des instants de transition de round, les deux requêtes étant espacées de 3 à 4 s.

### Mortal Kombat 3 (mesure du 2026-09-21, de 13:03 à 13:33 UTC)

361 cycles de 5 s (1 803 s, écart médian 5,00 s), 8 matchs vus dont **4 suivis en entier**. Réponses non-200 : 3, toutes des `204` de fin de match, aucune erreur réseau ou serveur.

- **Matchs plus courts que Mortal Kombat X** : 381 à 539 s de jeu (Mortal Kombat X : 589 à 870 s), visibles environ 360 à 370 s avant le début. Durée visible totale : 705 à 895 s (moyenne 796 s, contre 1 033 s).
- **Nombre de sélections** : 50,3 en moyenne (Mortal Kombat X : 27,3), à cause des marchés supplémentaires (section 15).
- **Changements de cote** : 63 transitions de 5 s sur 1 020 (6,2 %) contiennent au moins un changement de cote (Mortal Kombat X : 3,0 %). Même logique : les cotes bougent à la fin d'un round et jamais au milieu d'un round.
- **Fin de match : fenêtre beaucoup plus courte** : « Jeu terminé » et toutes les sélections verrouillées pendant **15 à 20 s seulement** (4 à 5 relevés à 5 s), contre 55 à 70 s pour Mortal Kombat X. Une seule requête ratée à ce moment fait perdre l'état final des cotes (elles sont de toute façon toutes verrouillées ; le score final reste disponible dans les résultats).
- **`statistic` et `gameEvents`** renvoient `204` sur un match terminé, comme pour Mortal Kombat X.

## 8. Estimation du volume (mesurée)

Mesures sur 3 matchs complets (enregistrement de 30 min, 361 cycles de 5 s) :

| | Match 1 (Quan Chi - Scorpion) | Match 2 (Liu Kang - Kotal Kahn) | Match 3 (Kung Jin - Kitana) | Moyenne |
|---|---|---|---|---|
| Relevés (5 s) | 231 | 217 | 175 | 208 |
| Lignes, **option A** (changements seulement) | 371 | 365 | 181 | **306** |
| Lignes, grille complète (option B) | 6 503 | 6 805 | 3 907 | **5 738** |
| Rapport A / B | 5,7 % | 5,4 % | 4,6 % | 5,3 % |

- **Option A : environ 88 000 lignes par jour** (288 matchs x 306). **Option B : environ 1,65 M lignes par jour** (288 x 5 738). Les hypothèses de la phase 1 (moins de 100 000 et environ 1,7 M) sont confirmées.
- Une ligne compte un changement de cote, de verrou ou l'apparition d'une sélection (les disparitions ne sont pas écrites comme lignes, ce sont des états déduits de l'absence).
- Ligue unique : environ 288 matchs par jour (283 à 289 par fenêtre de 24 h dans le service de résultats).
- **Mortal Kombat 3 (mesuré sur 4 matchs complets, 2026-09-21)** : 444 lignes par match en option A (392 à 463) et 8 067 en grille complète (rapport 5,5 %), pour 50,3 sélections affichées en moyenne. Extrapolation à 288 matchs par jour : environ **128 000 lignes par jour** en option A, environ 2,32 M en grille complète.
- **Deux ligues ensemble** : environ **216 000 lignes par jour** en option A (88 000 + 128 000), contre environ 3,97 M en grille complète (rapport 5,4 %). L'estimation prévue (180 000 à 230 000) est confirmée. Le stockage en option A est retenu.
- Requêtes : 2 `gamesByChamp` par cycle de 5 s (une par ligue), soit 0,4 requête par seconde, plus quelques `statistic` par match (uniquement à la fin d'un round). Bien en dessous de la limite prévue de 1 requête par seconde.
- **Bande passante** : une réponse `gamesByChamp` fait en moyenne **16,7 Ko décompressés** (maximum 23 Ko). Avec la compression HTTP (environ 2 Ko sur le fil), cela représente environ 40 à 50 Mo par jour ; sans compression, environ 290 Mo par jour. Dans les deux cas très en dessous des 4 To du VPS.

## 9. Architecture VPS recommandée

- **VPS de l'utilisateur (confirmé)** : 2 vCPU, 8 Go de RAM, 50 Go NVMe, 4 To de bande passante, situé aux **Pays-Bas**. Largement suffisant : Grafana et Prometheus tiennent sans difficulté, 2 vCPU restent le seul facteur à surveiller (un seul processus Python asynchrone, charge faible attendue).
- La localisation aux Pays-Bas est un risque à tester (voir section 12, point 6) : la plateforme dépend du paramètre `fcountry=84` (Cameroun) et d'une règle de redirection (`x-gw-rds`).
- Base : PostgreSQL 16 + extension TimescaleDB dans un seul conteneur (compression et rétention natives). À ce volume, PostgreSQL seul avec partitionnement mensuel suffirait. ClickHouse est disproportionné. SQLite pour le développement uniquement.
- Conteneurs Docker : `scraper`, `db`, `prometheus`, Grafana en option.
- **Redis supprimé** : `asyncio.Queue` et un checkpoint en base couvrent le besoin.
- **Rétention indéfinie (décision de l'utilisateur)** : aucune suppression de données (pas de politique `drop_chunks`). On compresse les données anciennes (Timescale, par exemple au-delà de 7 jours) pour limiter la croissance. Estimation de croissance (hypothèses : environ 100 octets par ligne, compression d'un facteur 8) : environ 20 Mo par jour bruts, soit environ 8 Go par an bruts et environ 1 Go par an compressés pour deux ligues. Confirmé par la mesure : environ 216 000 lignes par jour (section 8), soit environ 22 Mo par jour bruts. Les 50 Go du VPS suffisent donc pour plusieurs années.
- **Sauvegardes (décision de l'utilisateur : transferts mensuels du serveur vers sa machine locale)** : l'historique des cotes n'est pas reconstituable après coup (seuls les résultats le sont, section 2.2). Mise en place prévue : `pg_dump` compressé **quotidien sur le VPS** avec rotation (7 quotidiennes et 4 hebdomadaires), et récupération **mensuelle par l'utilisateur** vers sa machine (`scp` ou `rsync`, script fourni). **Point d'attention** : avec un seul transfert par mois, la perte maximale en cas de disparition du VPS serait d'environ 30 jours de cotes. Une récupération hebdomadaire réduirait ce risque sans coût notable (quelques dizaines de Mo par jour). Un test de restauration est prévu.
- ProxyPool implémenté mais désactivé par défaut (aucun besoin observé).

## 10. Schéma de base proposé

```text
events            (game_id PK, feed_id, league_id, sport_id, home_id, away_id, home_name, away_name,
                   start_time, status[scheduled|live|finished|vanished], first_seen, last_seen)
markets_dict      (group_id G, type_id T, group_label_fr, type_label_fr)   -- alimenté par le dictionnaire du CDN (section 2.3)
odds_snapshots    (hypertable sur ts)
                   (ts_server = U ou updateTs, collected_at, game_id, g, t, p, odds, blocked, is_center, round_no, line, latency_ms, source[v3|legacy])
                   PK/UNIQUE (game_id, g, t, p, ts_server)  -> déduplication naturelle
odds_last         (game_id, g, t, p -> odds)  -- détection de changement, reconstruite au démarrage
game_state        (game_id, ts_server, round, elapsed, score_home, score_away, raw_round_table jsonb)
results           (game_id PK, league_id, final_score, winner, rounds jsonb [{n, winner, finish R|F|B, seconds}], date_start, source, captured_at)
raw_payloads      (optionnel, compressé, courte rétention, pour rejouer après un changement de schéma)
checkpoints       (worker, key, value, updated_at)
```

Une nouvelle ligne n'est écrite que si la cote change. Une valeur n'est jamais écrasée.

Ajouts liés aux deux ligues : `league_id` (1252965 ou 2282406) dans `events`, `odds_snapshots`, `game_state` et `results` (jointure ou colonne dénormalisée pour filtrer rapidement), et dans `results.rounds` les colonnes `finish` étendues (R, F, B, Ba, Fr, An, Hk) plus les drapeaux `mercy_p1` et `mercy_p2`.

## 11. Architecture logicielle proposée

```text
Discovery (GetChampZip, 5 s)
   -> asyncio.Queue -> Collector workers (GetGameZip par match live/à venir)
   -> Parser (validation pydantic) -> Normalizer -> Deduplicator (odds_last)
   -> Storage queue -> PostgreSQL (batch)
```

- Transport : `httpx.AsyncClient`, connexions persistantes, HTTP/2. `RateLimiter` global d'environ 1 requête par seconde. Circuit breaker et backoff avec jitter par endpoint.
- **Délais d'attente** : 10 s par requête (99e centile mesuré : environ 2 s, pics isolés jusqu'à 16 s). Une requête en échec n'est pas rejouée immédiatement : on reprend au cycle suivant, sauf pendant les 15 à 20 s de fin de match de Mortal Kombat 3 où une nouvelle tentative rapide est utile.
- `AntiBotManager` : stratégies optionnelles (Session, UA, Proxy) branchées par configuration, sans dépendance du code métier.
- Playwright : uniquement pour l'exploration/CI, pas en production.
- **Source principale : endpoints v3** (ceux du site, cotes non mises en cache). `gamesByChamp` renvoie en **une seule requête** tous les matchs de la ligue avec scores, `updateTs` et toutes les cotes : 1 requête par cycle de 5 s suffit. **Source de secours : legacy** (`GetChampZip` + `GetGameZip`). Deux sources indépendantes pour rester résilient à un changement de structure ; le champ `source` est stocké.
- **`statistic`** : nécessaire uniquement pour le tableau des rounds (durée `T` de chaque round, absente des résultats). Il ne change qu'à la fin d'un round : l'appeler seulement quand le score change (environ 8 appels par match) au lieu de chaque cycle. Un `204` signifie « plus de données » (match terminé), pas une erreur.
- **ResultCollector** : appelle `result/web/api/v3/games` par fenêtres de 5 min alignées (au plus 2 jours par requête) pour compléter les résultats et **remplir l'historique passé** (au moins 90 jours) lors du premier lancement.
- **Dictionnaire** : téléchargé depuis le CDN au démarrage (`bets_model_map_full_fr.json` puis les chunks nécessaires) et rafraîchi périodiquement.
- **Alerting** : e-mail (SMTP Mailtrap, port 587 avec TLS) et Telegram (bot `@MortaleKombatbot`). Les alertes sont dédupliquées et limitées (une alerte identique au plus toutes les 30 min, plus un résumé quotidien) pour rester dans le quota réduit du domaine de démonstration Mailtrap (section 13, point 10) et éviter le harcèlement en cas de panne prolongée. Une alerte est aussi levée si la sauvegarde quotidienne est absente depuis plus de 36 h.

## 12. Risques techniques

1. ~~**Disparition du match à la fin**~~ **Atténué (mesuré)** : le match reste listé après le score final avec « Jeu terminé » et tout verrouillé, pendant **55 à 70 s pour Mortal Kombat X** mais seulement **15 à 20 s pour Mortal Kombat 3**. Les résultats officiels sont récupérables ensuite (section 2.2), donc seul l'état final des cotes (toutes verrouillées) peut être perdu.
2. ~~**Dictionnaire de marchés absent** du flux legacy.~~ **Résolu (2026-09-21)** : le dictionnaire officiel est publié sur le CDN (section 2.3). Reste à surveiller ses changements de contenu.
3. ~~**Endpoint legacy potentiellement déprécié**~~ **Traité** : le v3 (utilisé par le site) devient la source principale, le legacy reste en secours. Les deux répondent aujourd'hui.
4. **Rate limit et WAF** non testés depuis une IP de VPS ni sur la durée.
5. **Cotes possiblement générées** (matchs simulés). Leur usage en forecasting suppose de comprendre le générateur.
6. **Redirection géographique** (`x-gw-rds`) et dépendance au paramètre `fcountry` (84 pour le Cameroun). **Test multi-pays réalisé le 2026-09-21** (service public check-host.net, adresses de centres de données, endpoint `gamesByChamp`) : HTTP 200 depuis 20 pays sur 21 (Pays-Bas, Allemagne, France, Italie, Espagne, Portugal, Royaume-Uni, Suisse, Suède, Finlande, Autriche, États-Unis, Canada, Brésil, Inde non répondue, Indonésie, Singapour, Hong Kong, Japon, Israël, Turquie), sans redirection ni blocage. Cela réduit fortement le risque, sans le supprimer : il ne prouve pas que les adresses d'un hébergeur précis sont acceptées, et il ne vérifie que le code HTTP. À reconfirmer depuis l'IP réelle du VPS.
7. **Fuseau et horloge** : `U` est en epoch, `tzo=1` côté cookie. Tout stocker en UTC.
8. **Sélections qui apparaissent et disparaissent** : les lignes de certains marchés (ex. Total 6.5 absent plus tard dans le match) et les marchés par round changent au fil du match. Une sélection absente n'est pas une erreur de parsing (voir section 15).
9. **Drapeau de verrouillage `B:true`** : les cotes bloquées gardent une valeur `C` mais ne sont pas jouables. Ne pas les traiter comme des cotes disponibles (voir section 15).
10. **Sensibilité de l'API aux paramètres** : ordre alphabétique obligatoire, dates alignées sur 300 s, `champId` au singulier. Une régression côté serveur produit des 400 : à tester à chaque déploiement.
11. **Marchés propres à chaque ligue** : Mortal Kombat 3 expose des groupes absents de Mortal Kombat X (912, 10526, 10527, 10533) et un format de résultats différent. Le parseur ne doit pas coder les groupes en dur : s'appuyer sur le dictionnaire et stocker `(g, t, p)` bruts, avec une tolérance aux groupes inconnus (les enregistrer et lever une alerte, sans planter).
12. **Secrets partagés dans la conversation** : le mot de passe SMTP (capture d'écran) et le token du bot Telegram (message) ont été transmis dans la conversation. Ils ne sont pas recopiés dans ce fichier ; ils sont uniquement dans `.env` (non versionné). Les régénérer si la conversation est conservée ou partagée (section 19).
13. **E-mails d'alerte via le domaine de démonstration de Mailtrap** (`demomailtrap.co`) : d'après la documentation de Mailtrap telle que je la connais, ce domaine ne permet d'envoyer qu'à l'adresse du compte Mailtrap et avec un quota réduit. À vérifier dans le compte : l'adresse de réception doit être celle du compte. Pour un usage durable, vérifier un domaine propre.

## 13. Informations manquantes

1. ~~Types de données voulus~~ **Répondu** : ceux visibles sur les 3 captures fournies (voir section 15) : matchs, état du match (round, score, chrono), tableau des rounds (temps, vainqueur, type de victoire, type de finishing move), marchés et cotes (avec état verrouillé). **Ligues : Mortal Kombat X (`1252965`) et Mortal Kombat 3 (`2282406`) uniquement** (répondu).
2. ~~Spécifications du VPS~~ **Répondu** : 8 Go de RAM, 2 vCPU, 50 Go NVMe, 4 To de bande passante, Pays-Bas (voir section 9).
3. **Autorisation de sonder** l'API depuis l'IP du VPS et de tester un rythme supérieur à 1 requête par seconde. **Bloqué** : l'utilisateur n'a pas encore accès au VPS néerlandais (2026-09-21). Le développement et les tests se feront depuis la machine locale en attendant. Le pas de 5 s demandé équivaut à environ 0,8 requête par seconde, dans la limite prévue.
4. ~~**Playwright MCP**~~ **Installé, actif et utilisé** (section 14). Il a permis de lever les 400 du v3, de trouver le dictionnaire des libellés et l'endpoint des résultats.
5. ~~Pas de temps voulu~~ **Répondu** : **toutes les 5 s, pour le forecasting**. Cela remplace ma recommandation de départ (un snapshot par changement) : voir la proposition de stockage en section 16.
6. ~~Alerting~~ **Répondu** : canaux **e-mail et Telegram**. E-mail : SMTP Mailtrap (`live.smtp.mailtrap.io`, port 587, TLS), expéditeur sur le domaine `demomailtrap.co`, destinataire = l'adresse e-mail de l'utilisateur. Telegram : bot `@MortaleKombatbot` (nom « Mortal Kombat ») pour l'utilisateur `@Hohenheim_overlord`. Tous les identifiants sont dans `.env`, **non recopiés ici** (section 19).
7. ~~**Nouvelles questions issues du décodage**~~ **Résolues par le dictionnaire** (section 15) : `G=3037` = Flawless Victory dans Round, `CE:1` = ligne principale (`isCenter`), « Total 2 » = Total individuel du participant 2, fin de match = premier à 5 rounds gagnés. L'interprétation des Totaux est cohérente avec 3 matchs complets (section 15) mais sans confirmation officielle.
8. ~~Durée de rétention~~ **Répondu : indéfinie**, les données servant au forecasting (sections 9 et 10).
9. ~~Telegram : token et `chat_id`~~ **Fait** : token vérifié (bot `@MortaleKombatbot`), l'utilisateur a envoyé `/start`, le `chat_id` de sa conversation privée (`@Hohenheim_overlord`) a été lu via `getUpdates` et inscrit dans `.env`. Message de test envoyé ensuite (section 18, point 2).
10. ~~E-mail : adresses~~ **Répondu** : domaine d'expédition `demomailtrap.co`, destinataire = l'adresse e-mail de l'utilisateur. Adresse d'expédition retenue par défaut : `alerts@demomailtrap.co` (partie avant `@` à confirmer, sans conséquence en général sur le domaine de démonstration). **À vérifier par l'utilisateur** : que l'adresse de réception est bien celle du compte Mailtrap, condition d'envoi sur le domaine de démonstration (section 12, risque 13).
11. ~~Destination des sauvegardes~~ **Répondu** : transferts **mensuels** du serveur vers la machine locale de l'utilisateur (section 9, avec une recommandation de fréquence hebdomadaire).

## 14. Outillage : serveur MCP Playwright

**Installé le 2026-09-21** dans la configuration locale du projet `C:\Users\Mr NDJOCK LEVY\Desktop\Projet` (fichier `C:\Users\Mr NDJOCK LEVY\.claude.json`, portée « local », privée à ce projet).

- Commande enregistrée : `cmd /c npx -y @playwright/mcp@latest` (équivalent de `claude mcp add playwright -- cmd /c npx -y "@playwright/mcp@latest"`).
- Version du paquet `playwright` résolue : 1.63.0.
- Navigateur : Chromium (`chromium-1243`) installé dans `%LOCALAPPDATA%\ms-playwright`.
- Vérification : `claude mcp get playwright` indique **Connected**.

Incidents rencontrés :

| Problème | Cause | Résolution |
|---|---|---|
| Entrée créée avec `cmd C:/ npx …` | Git Bash convertit `/c` en `C:/` | Entrée supprimée et recréée depuis PowerShell |
| « connection timed out after 30000ms » | Téléchargement initial de npx et de Chromium supérieur à 30 s | Paquet et navigateur pré-chargés, la connexion passe ensuite |
| Bandeau « running npx playwright install without first installing dependencies » | Simple avertissement de Playwright, sans effet | Ignoré, Chromium est bien installé |
| `playwright` absent de `/mcp` après rechargement de VS Code | Entrée créée en portée « local » sous la clé de projet `C:/Users/…/Projet` (C majuscule), alors que l'extension VS Code désigne le dossier avec `c:` minuscule : l'entrée n'est pas retrouvée | Ajouté aussi en portée **utilisateur** (`claude mcp add --scope user playwright -- cmd /c npx -y "@playwright/mcp@latest"`), indépendante du chemin |

**Statut : actif** depuis le rechargement de VS Code (2026-09-21). Les outils du serveur (`browser_navigate`, `browser_network_requests`, `browser_evaluate`, `browser_run_code_unsafe`, etc.) sont disponibles.

Utilisation faite : chargement de la page du match et de la page des résultats, relevé des requêtes réseau réelles, écoute des WebSockets/SSE (aucun), test de requêtes depuis la page.

Fichiers créés par Playwright dans le projet : dossier `.playwright-mcp/` (captures d'accessibilité et journaux de console). Ils peuvent être supprimés et ne doivent pas être versionnés.

## 15. Décodage des marchés (confirmé par le dictionnaire officiel)

Sources : 3 captures d'écran de l'utilisateur, flux legacy et v3, dictionnaire français du CDN (section 2.3). Les libellés ci-dessous sont ceux du **dictionnaire officiel** : ils sont confirmés. Les interprétations restantes sont signalées comme telles.

### Tableau des rounds (captures 1 et 2) = `RoundTable` (`SC.S[RoundTable]` en legacy, `statistic.main.RoundTable` en v3)

| Colonne affichée | Champ | Exemple |
|---|---|---|
| Manche N | `R` | 1 |
| Temps | `T` (secondes) | 00:30 pour `T=30` |
| Victoire | `W` | Liu Kang |
| Type de victoire | `WT` | 0 (toujours 0 dans les relevés) |
| Types de finishing moves | `DI` : Regular, Fatality, Brutality | Regular |

En-tête de page : « 5ème round », score `3 : 1`, chrono `06:51` (`timer.timeSec`, temps écoulé en secondes). Dans les résultats, le type de finish est codé R (Regular), F (Fatality), B (Brutality).

### Groupes de marchés

| `G` | Libellé officiel | `T` : libellé officiel | `P` (legacy) / `eventParams.params` (v3) |
|---|---|---|---|
| 1 | 1x2 | 1 : V1, 3 : V2 (le nul `T=2` n'est pas proposé) | aucun |
| 1050 | Victoire dans le Round | 2140 : V1 dans le Round, 2141 : V2 dans le Round | numéro du round, ex. `["(6)"]` |
| 1066 | Mode de Victoire Dans Le Round | 4057 : Fatality, 4058 : Brutality, 4059 : No Finish | numéro du round |
| 1074 | Durée du Round | 2170 : Plus de, 2171 : Moins de | `P = round x 100 + ligne / 100` (ex. 600.235 = round 6, ligne 23,5 s) ; v3 : `["6","23.5"]` |
| 3533 | Fatality dans Round | 4929 : Oui, 4930 : Non | numéro du round |
| 3037 | Flawless Victory dans Round | 4055 : Oui, 4056 : Non | numéro du round |
| 17 | Total | 9 : Plus de, 10 : Moins de | la ligne, ex. `7.5` |
| 15 | Total 1 (Total Individuel 1) | 11 : Plus de, 12 : Moins de | la ligne, ex. `2.5` |
| 62 | Total 2 (Total Individuel 2) | 13 : Plus de, 14 : Moins de | la ligne, ex. `2.5` |

Neuf groupes distincts ont été observés au total (1, 15, 17, 62, 1050, 1066, 1074, 3037, 3533). Seuls 7 à 8 sont présents à un instant donné : les groupes 15 et 3037 apparaissent et disparaissent selon le moment du match.

### Marchés propres à Mortal Kombat 3 (dictionnaire officiel et enregistrement de 30 min)

Mortal Kombat 3 affiche 9 à 11 groupes (Mortal Kombat X : 7 à 8) et 50,3 sélections en moyenne. Groupes absents de Mortal Kombat X, avec le comportement mesuré sur 4 matchs complets (641 relevés) :

| `G` | Libellé officiel | `T` : libellé officiel | Paramètre observé | Comportement mesuré |
|---|---|---|---|---|
| 912 | Totaux supplémentaires | 4060 / 4061 : Total Fatalities Plus de / Moins de ; 4062 / 4063 : Total Brutalities ; 13552 / 13553 : Total Amitié | lignes de 0.5 à 6.5 | Présent 100 % du temps. **Le plus mobile** : 234 changements de cote et 426 changements de verrou sur 4 matchs. |
| 10526 | Total de Babality | 14009 : Plus de ; 14010 : Moins de | lignes 0.5 et 1.5 | Présent 100 % du temps, 32 changements de cote. |
| 10527 | Total d'Animality | 14011 : Plus de ; 14012 : Moins de | ligne 0.5 | Présent avant le début (88 %), retiré en cours de match (19 % du temps de jeu), absent d'un match sur 4. 2 changements de cote seulement. |
| 10533 | Mercy dans le Round | 14023 : Les deux joueurs ; 14024 : Joueur 1 uniquement ; 14025 : Joueur 2 uniquement ; 14026 : Aucun des deux | numéro du round | Présent 100 % du temps. Remplacé à chaque round (88 apparitions et 88 disparitions), donc jamais repricé : 0 changement de cote. Le paramètre vaut les rounds joués + 1 ou + 2 (375 et 266 relevés), comme pour Mortal Kombat X. |

Groupes communs avec Mortal Kombat X (mêmes libellés) dont le contenu diffère :

- **`G1066` Mode de Victoire Dans Le Round** : 7 issues au lieu de 3 : 4057 Fatality, 4058 Brutality, 4059 No Finish, **13550 Amitié**, **14003 Babality**, **14005 Animality**, **14007 Hara-Kiri**.
- **`G15` Total 1 et `G62` Total 2** : présents seulement 61 % et 75 % du temps de jeu (lignes retirées progressivement), lignes de 0.5 à 3.5.
- **`G17` Total** : présent 100 % du temps, lignes 5.5 à 8.5.
- Les groupes `1`, `1050`, `3037` se comportent comme dans Mortal Kombat X.

Aucun groupe inconnu du dictionnaire : les 11 groupes vus ont un libellé officiel (les groupes 15, 17 et 62 sont dans le chunk 0 du dictionnaire).

### Types de finish (tableau des rounds et résultats)

| `DI` (tableau des rounds) | Code (résultats) | Mortal Kombat X (2 084 rounds sur 24 h) | Mortal Kombat 3 (2 101 rounds sur 24 h) |
|---|---|---|---|
| Regular | `R` | 57,9 % | 48,4 % |
| Fatality | `F` | 28,0 % | 39,3 % |
| Brutality | `B` | 14,1 % | 8,0 % |
| Babality | `Ba` | absent | 2,0 % |
| Friendship | `Fr` | absent | 1,6 % |
| Animality | `An` | absent | 0,6 % |
| Hara-Kiri | `Hk` | absent | 0,1 % |

- **Les deux ligues n'ont pas la même distribution** de finishes (Fatality 28 % contre 39 %) : ne pas mélanger leurs données sans variable de ligue dans les modèles.
- **`FW`** (booléen) est présent sur chaque round ; **`WT`** vaut `'0'` sur 42 rounds sur 43 et `'1'` une fois (sur un round Regular). Sa signification n'est pas établie, et ce n'est pas le drapeau Mercy (le round concerné avait `M- / M-`). À stocker brut.

### Points structurants pour le modèle de données

- **Verrouillage (cadenas)** : `B:true` (legacy) = `blocked:true` (v3). Constaté sur 19 sélections sur 19 en fin de match. Le stockage conserve ce drapeau (`blocked`) pour distinguer cote suspendue et cote jouable.
- **Ligne principale** : `CE:1` (legacy) = `isCenter:true` (v3). Confirmé : mêmes sélections marquées dans les deux flux.
- **Paramètres déjà décodés en v3** : `eventParams.params` donne round et ligne sans avoir à décoder `P`. Le décodage de `P` n'est nécessaire que pour la source legacy.
- **Identité d'une sélection** : `(G, T, P)`. `P` change à chaque round pour les marchés par round. Une sélection qui disparaît (ligne devenue impossible) est un événement normal, pas une erreur de parsing.
- **Compteurs (legacy)** : `MEC` (nombre de cotes par type de marché), `EC` (total de cotes), `EGC` (nombre de groupes) : utiles pour valider le parsing.
- **Règle de fin de match (confirmée par les résultats)** : premier à **5 rounds gagnés**, donc 5 à 9 rounds. Les lignes de Total (6.5, 7.5, 8.5) sont compatibles avec ce nombre de rounds.
- **Totaux : interprétation cohérente avec 3 matchs complets** (sans confirmation officielle) : « Total » (G17) = nombre total de rounds du match ; « Total 1 » (G15) et « Total 2 » (G62) = rounds gagnés par le participant 1 et 2. Exemples : dans le match 5-0, il ne reste que la ligne 5.5 du Total après 4 rounds (le total sera 5) et la ligne 1.5 du Total 2 (le participant 2 finit à 0) ; dans le match 5-3, la ligne 3.5 subsiste dans G62 (le participant 2 termine à 3 rounds gagnés).
- **Retrait des lignes** : le bookmaker retire des lignes avant qu'elles soient décidées (ex. ligne 6.5 du Total retirée après 4 rounds joués alors que le total final peut encore aller de 5 à 9). Une ligne qui disparaît n'indique donc pas son résultat.
- **Round visé (mesuré sur 3 matchs, 1 344 relevés)** : le paramètre de round `p` vaut toujours (rounds joués + 1) ou (rounds joués + 2), **jamais un round déjà joué**. Par rapport à la période affichée (`currentPeriod`), `p` vaut la période (547 relevés) ou la période + 1 (975 et 557 relevés, selon le cas). Ne pas se fier au libellé de la période pour dater un marché : utiliser le nombre de rounds joués (somme des scores).
- **Verrouillage en cours de match** : voir section 7 (déverrouillé à la fin d'un round, verrouillé au début du suivant, entièrement verrouillé à la fin du match).
- **Durée de vie d'un match** : le libellé `SLS` (« 9 minutes », « 14 minutes ») est le temps écoulé, cohérent avec `TS`.

## 16. Stockage pour le pas de 5 s : option A retenue

Contrainte : l'utilisateur veut une série temporelle **toutes les 5 s** pour le forecasting. Le cache serveur (`max-age=5`) rafraîchit la donnée au plus toutes les 5 s, et `U` n'avance pas exactement de 5 s entre deux relevés (observé : 3 à 7 s).

**Décision de l'utilisateur (2026-09-21) : option A.** Mesures : environ 306 lignes par match, soit environ 88 000 lignes par jour (section 8). Deux options avaient été comparées, compatibles avec le VPS (8 Go, 50 Go NVMe) :

| Option | Principe | Avantage | Inconvénient |
|---|---|---|---|
| **A (retenue)** | Sonder toutes les 5 s. Écrire une ligne dans `odds_snapshots` **seulement quand la cote, le verrou ou la présence change**. Produire la grille de 5 s à la lecture (`time_bucket_gapfill` + report de la dernière valeur, ou vue matérialisée). | Historique exact et sans doublon, stockage minimal, grille reproductible. | Requêtes de lecture un peu plus complexes. |
| **B** | Écrire toutes les sélections à chaque relevé (environ 1,3 à 1,7 M lignes par jour). | Lecture directe, grille immédiate. | Volume plus élevé (compressé par Timescale, tient sur 50 Go), beaucoup de lignes identiques. |

Dans les deux cas : déduplication sur `(game_id, g, t, p, ts_server)` avec `ts_server = U` (legacy) ou `updateTs` du match (v3) pour ignorer les réponses identiques renvoyées deux fois, colonne `blocked`, colonne `collected_at`, et `latency_ms`. **Attention** : `updateTs` change presque à chaque relevé et ne peut pas servir à ignorer une réponse inchangée. Comparer le contenu (cote, verrou) avec la dernière valeur connue avant d'écrire.

## 17. Enregistrement complet à 5 s (2026-09-21)

- **Outil** : script temporaire Python (bibliothèque standard uniquement) qui interroge `gamesByChamp` toutes les 5 s et `statistic` pour chaque match visible (plus un `gameEvents` tous les 6 cycles pour comparaison). Réponses brutes stockées en JSONL avec horodatage de collecte et latence. Arrêt automatique au-delà de 30 erreurs.
- **Résultat** : 1 776 lignes, 361 cycles, 1 805 s, 5 réponses 204 (fins de match) et aucune erreur réseau ou serveur.
- **Conservation** : le fichier brut (environ 7,4 Mo) est dans le dossier temporaire de la session et n'est **pas** dans le projet. Il peut servir de jeu de données de test pour le futur collecteur (matchs complets, fins de match, transitions de round).
- **Deuxième enregistrement (Mortal Kombat 3, 2026-09-21, 13:03 à 13:33 UTC)** : même outil, ligue `2282406`. 1 450 lignes, 361 cycles, 1 803 s, 3 réponses 204 (fins de match), aucune erreur. Fichier brut d'environ 8,5 Mo, lui aussi hors du projet.

## 18. Prochaines étapes

**Réalisé** : réponses de l'utilisateur (données, VPS, pas de 5 s, alerting, option A, rétention indéfinie, deux ligues), décodage des marchés, exploration Playwright (risques 2 et 3 résolus), enregistrements de 30 min à 5 s sur Mortal Kombat X et Mortal Kombat 3 (volumes, marchés supplémentaires, codes de finish).

**Reste à faire :**

1. **Test depuis l'IP du VPS néerlandais** (risque 6) : impossible pour le moment (pas d'accès au VPS). À faire avant toute mise en production.
2. **Tests des canaux d'alerte** : envoyés le 2026-09-21 avec l'accord de l'utilisateur. Telegram : message accepté par l'API (HTTP 200). E-mail : envoi accepté par le serveur SMTP Mailtrap, aucun destinataire refusé. **À confirmer par l'utilisateur** : réception effective du message Telegram et de l'e-mail (dossier spam compris). Une acceptation SMTP ne garantit pas la livraison.
3. ~~Enregistrer 30 min de Mortal Kombat 3~~ **Fait** (section 17). Reste facultatif : un enregistrement plus long (plusieurs heures) pour rassurer sur des cas rares (rounds Animality ou Hara-Kiri, groupes qui apparaissent seulement de temps en temps).
4. ~~Valider l'architecture~~ **Validée le 2026-09-21** (décisions D1 à D9 de [docs/architecture.md](docs/architecture.md), dont la récupération hebdomadaire des sauvegardes). **Implémentation en cours** : étape 3 terminée (section 20), étape 4 (prototype du collecteur) à venir.

## 19. Secrets et configuration

- **Fichiers créés à la racine du projet** (2026-09-21) :
  - `.env` : valeurs réelles (SMTP, token du bot Telegram). **Non versionné.**
  - `.env.example` : mêmes variables **sans valeurs**, à versionner.
  - `.gitignore` : exclut `.env`, `.env.*` (sauf `.env.example`), `.playwright-mcp/`, `__pycache__/`, `*.pyc`, `*.bak`.
- **Variables** : `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_FROM`, `EMAIL_TO`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USERNAME`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` (généré), `GRAFANA_ADMIN_PASSWORD` (généré, étape 8 ; Grafana n'est joignable qu'en local ou par tunnel SSH, section 9). Le `chat_id` a été renseigné automatiquement après le `/start` de l'utilisateur.
- **Jamais dans ce fichier ni dans le code** : le mot de passe SMTP et le token du bot. Les journaux masquent ces valeurs.
- **Recommandation** : le mot de passe SMTP a été transmis par capture d'écran et le token Telegram par message dans la conversation. Si cette conversation est conservée ou partagée, régénérer le mot de passe dans Mailtrap et révoquer le token avec `/revoke` auprès de `@BotFather`, puis mettre à jour `.env`.
- **Sur le VPS** : `.env` transféré par un canal sûr (`scp`), droits `600`, propriétaire non root, jamais copié dans une image Docker (fourni au conteneur par `env_file` ou variables d'environnement).
- **Repère** : le dossier du projet n'est pas encore un dépôt Git. Si `git init` est fait plus tard, le `.gitignore` est déjà en place ; vérifier avec `git status` que `.env` n'apparaît pas avant le premier commit.

## 20. Règle de conduite : pas d'évasion anti-bot

**Décision de l'utilisateur (2026-09-21), après une demande initiale contraire.** L'utilisateur a
d'abord demandé que le collecteur mette en œuvre des techniques d'évasion de détection pour
« fonctionner quelle que soit la technique anti-scraping mise en place » : rotation de User-Agent,
copie de jetons ou d'en-têtes de session, imitation d'empreinte TLS, simulation de comportement
humain, résolution automatique de CAPTCHA, et changement d'identité pour continuer après un blocage.

J'ai refusé de construire ces capacités, pour deux raisons : elles ne répondent à aucun besoin réel
(la reconnaissance n'a trouvé aucune protection anti-bot sur ce site, sections 5 et 12) et elles
constituent un outil général de contournement de détection, réutilisable contre n'importe quel site.
**L'utilisateur a accepté cette limite** et demandé de poursuivre sur cette base.

Règle appliquée à partir de l'étape 4 :
- **identification honnête et fixe** : un User-Agent descriptif (`OddsCollector/1.0`), jamais de
  rotation ni d'imitation de navigateur, aucun en-tête ou jeton copié depuis un navigateur ;
- **un blocage (403/429) n'est jamais contourné** : il lève une erreur dédiée (`BlockedError`),
  sans nouvelle tentative automatique ni changement d'identité ; l'arrêt et l'alerte (étape 8)
  restent la seule réponse prévue ;
- les reprises automatiques ne couvrent que les pannes transitoires (timeout, coupure réseau, 5xx),
  jamais un refus explicite du site ;
- le `ProxyPool` (section 9, D5) garde son rôle d'origine : joindre le site depuis une IP acceptée
  si la localisation géographique pose problème, pas échapper à un blocage ciblé.

## 21. Implémentation : journal d'avancement

### Étape 3 : schéma de base de données (2026-09-21) : terminée

**Fichiers créés** (racine du projet) :

| Fichier | Rôle |
|---|---|
| `migrations/001_extensions_reference.sql` | Extension TimescaleDB, `leagues` (2 ligues), `markets_dict` |
| `migrations/002_events_and_results.sql` | `events`, `game_state`, `results`, `round_results` |
| `migrations/003_odds_snapshots.sql` | `odds_snapshots` (hypertable, chunks de 7 jours, compression après 7 jours), vue `odds_latest` |
| `migrations/004_operations.sql` | `collection_log` (hypertable), `dead_letter`, `checkpoints` |
| `src/collector/storage/migrate.py` | Exécuteur de migrations (transaction par migration, verrou, somme de contrôle, rejouable) |
| `src/collector/storage/queries.py` | Requêtes SQL idempotentes du stockage |
| `tests/integration/` | 25 tests d'intégration (base réelle) |
| `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `Makefile` | Base TimescaleDB (aucun port publié) et exécution des tests |
| `requirements.txt`, `requirements-dev.txt`, `pyproject.toml` | Versions épinglées : `asyncpg 0.31.0`, `pytest 9.1.1`, `pytest-asyncio 1.4.0` ; image `timescale/timescaledb:2.30.1-pg16` ; Python 3.12 dans Docker |

**Ce que les tests prouvent** : les migrations s'appliquent, se rejouent sans effet, refusent une migration modifiée, absente ou hors ordre, et annulent une migration en échec ; les cotes ne sont jamais écrites deux fois ni écrasées ; un retrait de sélection est enregistré (`odds` NULL) ; la vue `odds_latest` renvoie le dernier état ; un match terminé ne redevient pas « live » et `last_seen` ne recule pas ; les rounds se complètent sans écrasement ; les contraintes rejettent cotes non positives, sources inconnues, matchs ou ligues inexistants, scores égaux ; un JSON corrompu se stocke en lettre morte ; les hypertables ont les bons intervalles (7 et 30 jours) et les politiques de compression existent (7 et 14 jours) ; **un ancien chunk compressé garde ses données et rejouer une ligne dedans ne crée pas de doublon** (vérifié avec TimescaleDB 2.30.1).

**Résultat** : 25 tests passés (2 min 54 s sur cette machine, dont l'essentiel est le coût de création de bases). Deux corrections en cours de route, toutes deux dans les tests : un test appliquait la migration 003 sans la 002 dont elle dépend (corrigé avec des migrations synthétiques) ; la copie de la base modèle échouait de façon aléatoire parce que l'ordonnanceur de TimescaleDB garde une session ouverte sur chaque base (corrigé : session coupée et nouvel essai).

**Commandes** (depuis la racine du projet ; sous Windows sans `make`) :

```text
docker compose up -d db                                                       # démarre la base
docker compose --profile test run --rm tests                                  # lance les 25 tests
docker compose --profile test run --rm tests python -m collector.storage.migrate   # migre la base 'odds'
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'        # console SQL
docker compose stop db                                                        # arrête la base (données conservées)
```

**Points d'attention** :
- Les variables de la base (`POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`) sont dans `.env`, le mot de passe est aléatoire et n'est écrit nulle part ailleurs.
- Le conteneur tourne avec le superutilisateur de la base. Avant la production (étape 10), créer un rôle applicatif aux droits limités (lecture et écriture, sans droit de modifier le schéma) ; ce rôle exige un mot de passe issu de `.env`, donc un script d'initialisation plutôt qu'une migration statique.
- Disque de la machine de développement presque plein (environ 8 Go libres au début de l'étape) : les images Docker (TimescaleDB, Python) occupent quelques Go.
- La grille de 5 s pour le forecasting (fonction SQL et `scripts/export_grid.py`, section 16) reste à écrire ; elle n'était pas dans le périmètre de cette étape.

### Étape 4 : prototype du collecteur (2026-09-21) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `config/leagues.yaml`, `config/settings.yaml` | Les deux ligues ; réglages de transport (User-Agent honnête, débit, reprises, coupe-circuit) |
| `src/collector/transport/errors.py` | `ServerError` (transitoire, réessayée), `BlockedError` (jamais réessayée), `ParserError` |
| `src/collector/transport/ratelimit.py` | `RateLimiter` (espacement minimal), `CircuitBreaker` (3 états) |
| `src/collector/transport/http.py` | Client HTTP honnête : une identité fixe, tri alphabétique des paramètres, reprises bornées avec attente exponentielle et facteur aléatoire |
| `src/collector/sources/v3/models.py` | Modèles pydantic de `gamesByChamp`, tolérants aux champs et groupes inconnus |
| `src/collector/sources/v3/client.py` | Appel de l'endpoint et validation, erreurs transformées en `ParserError` |
| `src/collector/normalize.py` | Décodage `eventParams` (round/ligne), aplatissement en lignes de cotes |
| `src/collector/dedupe.py` | `ChangeDetector` (option A), avec préchargement pour la reprise |
| `src/collector/storage/writer.py` | Écriture idempotente (match, état, cotes) |
| `src/collector/pipeline.py` | Enchaînement d'un cycle, réutilisable par le futur ordonnanceur |
| `src/collector/prototype.py` | Exécutable de démonstration bornée (usage manuel, pas un service permanent) |
| `tests/fixtures/*.json` | Un match complet par ligue, extrait des enregistrements réels (décision D8) |
| `tests/unit/`, `tests/integration/test_pipeline.py` | 33 tests supplémentaires (62 au total avec l'étape 3) |

**Dépendances ajoutées** (versions épinglées) : `httpx 0.28.1`, `pydantic 2.13.5`, `PyYAML 6.0.3`.

**Bug trouvé et corrigé avant la fin de l'étape** : le détecteur de changement réécrivait une
sélection retirée à **chaque cycle suivant, pour toujours**, au lieu d'une seule fois au moment du
retrait. Sur un match long, cela aurait progressivement annulé le bénéfice de l'option A (autant de
lignes qu'une grille complète pour les sélections retirées tôt). Trouvé par un test comparant un
rejeu isolé après préchargement à un rejeu continu : l'écart correspondait exactement aux sélections
déjà retirées avant la moitié du match. Corrigé : une sélection retirée n'est plus signalée que lors
de la transition, une réapparition ultérieure étant traitée comme une nouvelle sélection.

**Ce que les tests prouvent** : le parseur accepte un champ ou un groupe de marché inconnu sans
planter, et rejette un JSON invalide ou un champ requis manquant ; le décodage round/ligne est
correct sur les cas mesurés (valeur seule = round ou ligne selon le groupe, deux valeurs = round puis
ligne) ; un blocage 403/429 ne déclenche aucune nouvelle tentative (une seule requête reçue) ; une
panne transitoire est réessayée puis réussit ; le coupe-circuit s'ouvre après le nombre d'échecs
configuré et bloque les appels suivants sans toucher le réseau ; **un match complet de chaque ligue,
rejoué depuis l'enregistrement réel, produit un état final en base identique au dernier relevé** ;
les marchés propres à Mortal Kombat 3 (Mercy, Babality, Totaux supplémentaires) sont stockés sans
erreur ; rejouer le même enregistrement ne crée aucun doublon ; précharger l'état depuis la base
avant de reprendre un relevé déjà connu n'écrit rien de plus.

**Résultat** : 62 tests passés, confirmés sur deux exécutions complètes indépendantes.

**Nettoyage avant la connexion du dépôt Git (2026-09-21)** : un module `src/collector/config.py` (chargement typé de `config/*.yaml`, avec détection des identifiants de ligue en double) avait été écrit mais laissé orphelin, `prototype.py` chargeant le YAML lui-même en double. Fusionné en gardant la version typée ; `prototype.py` et `tests/unit/test_config.py` mis à jour en conséquence. **64 tests passés** après la fusion.

**Commandes** (depuis la racine du projet) :

```text
docker compose up -d db
docker compose --profile test build tests        # après un changement de requirements.txt
docker compose --profile test run --rm tests pytest -q
docker compose stop db
```

**Points d'attention pour la suite** :
- `prototype.py` n'est pas l'ordonnanceur final : il tourne un nombre borné de cycles pour une seule
  ligue, sans reprise complète après redémarrage ni surveillance. L'ordonnanceur permanent, les deux
  ligues en parallèle, et la relecture complète de l'état au démarrage restent l'étape 6.
- La source de secours (legacy), le dictionnaire de libellés (téléchargement CDN), et les résultats
  officiels avec rattrapage de l'historique restent l'étape 5.
- Le champ `WT` du tableau des rounds (signification non établie, Memoire.md section 15) est stocké
  brut sans interprétation, comme prévu.
- Le test d'identification honnête (`User-Agent` fixe, jamais de rotation) est maintenant automatisé
  et fera partie de la suite à chaque exécution future.

### Étape 5 : dictionnaire, résultats et rattrapage (2026-09-21) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `src/collector/dictionary.py` | `MarketDictionary` : chargement paresseux et mis en cache des chunks du CDN, résolution des libellés, mise à jour de `markets_dict` pour les marchés jamais vus |
| `src/collector/results.py` | Modèles des résultats, analyse du score (`parse_score`), découpage en fenêtres alignées (`iter_windows`), `backfill_results` (rattrapage ancré une fois pour toutes) et `reconcile_recent` (réconciliation périodique, idempotente) |
| `tests/fixtures/dictionary/`, `tests/fixtures/results_*.json` | Table des chunks et neuf chunks réellement téléchargés lors de la reconnaissance ; échantillons de résultats des deux ligues |
| `tests/unit/test_dictionary.py`, `tests/unit/test_results.py`, `tests/integration/test_results.py` | 45 tests supplémentaires (109 au total) |

**Deux bugs trouvés et corrigés avant la fin de l'étape**, tous deux avant d'avoir touché au réseau réel (détectés par les tests, sur les fixtures) :

1. **Dérive de la grille de rattrapage.** Ma première version recalculait la borne « maintenant » à
   chaque appel de `backfill_results`. Après une reprise, la grille de fenêtres se serait décalée
   par rapport au premier passage, et l'intervalle entre l'ancien et le nouveau « maintenant »
   n'aurait jamais été couvert. Corrigé : l'« ancre » (borne haute du rattrapage) est fixée une
   seule fois, à la toute première exécution, et conservée dans le point de contrôle ; les reprises
   la réutilisent sans jamais la recalculer. La couverture des résultats les plus récents est
   assurée séparément par `reconcile_recent`, rejouée sans risque toutes les 5 min (idempotente,
   sans point de contrôle).
2. **Incohérence réelle du site, déjà repérée en reconnaissance mais pas reportée dans le code.**
   Les groupes Total, Total 1 et Total 2 (17, 15, 62) vivent en réalité dans le chunk 0 du
   dictionnaire, alors que la table des intervalles désigne un autre chunk pour ces identifiants.
   `MarketDictionary.label_for` essaie maintenant le chunk indiqué par la table, puis le chunk 0 en
   repli, avant de conclure qu'un marché est inconnu. Une seconde incohérence, mineure, a aussi été
   confirmée par les tests : le libellé du groupe « Total d'Animality » porte une apostrophe, celui
   de ses sélections non (« Total Animality Plus de () ») — stocké tel quel, sans correction.

**Ce que les tests prouvent** : les libellés officiels de 15 marchés (couvrant les deux ligues) sont
résolus correctement à partir des fichiers réellement téléchargés du CDN ; un chunk n'est récupéré
qu'une seule fois même s'il sert plusieurs résolutions ; un groupe ou un type inconnu renvoie une
absence de résultat sans lever d'erreur ; l'analyse du score gère les codes de finish à une ou deux
lettres (R, F, B, Ba, Fr, An, Hk), le suffixe Mercy de Mortal Kombat 3, un round illisible isolé
(ignoré sans faire échouer tout le résultat), et rejette un score totalement méconnaissable ou à
égalité ; le découpage en fenêtres respecte l'alignement sur 300 s et la limite de 2 jours, sans trou
ni recouvrement ; le rattrapage reprend exactement où il s'est arrêté après une interruption
simulée en plein milieu (panne réseau), sans jamais perdre de fenêtre ni redemander ce qui a déjà
réussi ; le stockage des résultats est idempotent et complète le tableau des rounds déjà connu sans
l'écraser, y compris quand un résultat est ignoré (score illisible) sans faire échouer le lot.

**Résultat** : 109 tests passés, confirmés sur deux exécutions complètes indépendantes.

**Commandes** : inchangées depuis l'étape 4 (voir plus haut).

**Points d'attention pour la suite** :
- Ni le dictionnaire ni le rattrapage ne sont encore appelés automatiquement : ce sont des briques
  prêtes à l'emploi, à brancher sur l'ordonnanceur permanent (étape 6), avec les intervalles déjà
  configurés dans `config/settings.yaml` (rafraîchissement du dictionnaire toutes les 6 h,
  réconciliation des résultats toutes les 5 min, rattrapage de 90 jours au premier lancement).
- `days_back` doit rester constant d'un appel à l'autre pour une même ligue (documenté dans le code) :
  le changer nécessite d'effacer le point de contrôle de cette ligue.
- Le champ `WT` et le sens exact du drapeau `CE`/`isCenter` restent non interprétés, stockés bruts.

### Étape 6 : ordonnanceur permanent, concurrence, reprise complète (2026-09-21) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `src/collector/scheduler.py` | `Scheduler` : boucles de sondage par ligue, file bornée vers une boucle d'écriture unique, tâches périodiques (dictionnaire, réconciliation), politique de blocage |
| `src/collector/main.py` | Point d'entrée de production : assemble le tout, gère SIGINT/SIGTERM pour un arrêt propre |
| `tests/integration/test_scheduler.py` | 7 tests (concurrence, file bornée, reprise, blocage) |
| `migrations/005_allow_tied_results.sql` | Corrige une hypothèse fausse du schéma (voir plus bas) |
| `Dockerfile` (cible `runtime`), `docker-compose.yml` (service `scraper`) | Image et service minimaux pour lancer le collecteur (durcissement complet à l'étape 10) |

**Architecture retenue** : les boucles de sondage réseau (une par ligue) et l'écriture en base sont
découplées par une file bornée (`asyncio.Queue`) et une seule boucle d'écriture. Si l'écriture prend
du retard, la file se remplit et les producteurs sont mis en attente (contre-pression), sans perte
de données et sans notification supplémentaire nécessaire. Chaque méthode `*_once` (`poll_once`,
`write_once`, `refresh_dictionary_once`, `reconcile_results_once`, `backfill_once`) est un pas isolé
et testable indépendamment du minutage réel ; `run()` les enchaîne en boucles permanentes.

**Un vrai problème de conception trouvé et corrigé avant tout test** : un blocage pendant le
rattrapage ou la réconciliation des résultats n'était géré nulle part et aurait fait planter tout
le programme avec une exception non rattrapée, au lieu de s'arrêter proprement. Corrigé avec une
politique cohérente : un blocage du site principal arrête tout l'ordonnanceur (odds, résultats et
sondage, qui partagent tous la même identité et la même adresse IP) ; un blocage du CDN du
dictionnaire n'arrête que le rafraîchissement des libellés, sans affecter la collecte des cotes.

**Un vrai problème de performance trouvé lors d'un essai réel borné dans le temps contre le site**
(identification honnête, débit d'une requête par seconde comme configuré) : le stockage des
résultats écrivait chaque résultat et chaque round un par un. Pour une fenêtre de 2 jours (jusqu'à
environ 578 matchs, section 8), cela représentait plus de mille allers-retours vers la base. Sur
cette machine de développement ralentie, l'écart entre deux fenêtres de rattrapage a atteint 7 à
8 minutes, et un plafond de 150 s posé pour l'essai n'a pas suffi à arrêter le programme à temps
(le conteneur a dû être arrêté manuellement après 25 min). Corrigé : `store_results` regroupe
maintenant ses écritures (`executemany`), comme le fait déjà l'écriture des cotes. Un second essai,
réduit à 4 jours de rattrapage, a confirmé la correction : le rattrapage des deux ligues s'est
terminé en moins de 10 s, suivi d'un passage normal en collecte continue jusqu'à l'arrêt (`timeout`)
au bout de 60 s. Le rattrapage vérifie désormais aussi une demande d'arrêt entre chaque fenêtre
(`should_stop`), pour s'interrompre rapidement plutôt que d'aller au bout de toutes les fenêtres
restantes ; un délai de grâce de 90 s a été ajouté au service Docker en conséquence.

**Une découverte réelle, faite pendant ce même essai, qui a corrigé une hypothèse fausse du
schéma** : un match Mortal Kombat 3 (`754255087`) s'est terminé **2:2**, une égalité que la
reconnaissance (section 2.2) supposait impossible pour ce sport, et que le schéma de l'étape 3
interdisait explicitement (contraintes `results_scores_chk` et `results_winner_chk`). Ce résultat
était silencieusement perdu (rejeté par `parse_score`, jamais écrit en base), alors que l'objectif
posé par l'utilisateur est de ne perdre aucune donnée utile au forecasting. Corrigé par la migration
005 : `winner` devient `NULL` exactement quand `final_score1 = final_score2` (nouvelle contrainte
`results_winner_tie_chk`), et `parse_score` accepte désormais une égalité au lieu de la rejeter.
Cette égalité est probablement une interruption ou un incident technique côté site plutôt qu'une
règle du jeu normale, mais elle est bien réelle et désormais conservée.

**Ce que les tests prouvent** : deux ligues sont sondées et écrites indépendamment sans interférence
entre leurs détecteurs de changement ; la file bornée met effectivement le producteur en attente
quand elle est pleine, puis le libère dès qu'une place se dégage ; un redémarrage (nouveaux
détecteurs vides, préchargement depuis la base) ne réécrit rien de ce qui est déjà connu, mais
détecte toujours un vrai changement survenu entre-temps ; un blocage arrête l'ordonnanceur sans
lever d'exception non gérée, aussi bien pour un cycle isolé que pour `run()` dans son ensemble,
y compris quand le blocage survient dès le tout premier appel (avant même le premier cycle de
sondage) ; un arrêt explicite (`stop()`, donc un futur SIGTERM) met fin à `run()` en un temps borné ;
le dictionnaire et la réconciliation des résultats atteignent bien toutes les ligues configurées.

**Résultat** : 117 tests passés, confirmés sur deux exécutions complètes indépendantes, plus un
essai réel réussi contre le site (rattrapage, sondage des deux ligues, arrêt propre).

**Commandes** (nouvelles depuis l'étape 6) :

```text
docker compose build scraper                                   # construit l'image du collecteur
docker compose up scraper                                      # le lance (Ctrl+C pour l'arrêter proprement)
docker compose run --rm --entrypoint sh scraper -c "timeout 60 python -m collector.main"  # essai borné dans le temps
```

**Points d'attention pour la suite** :
- L'image `runtime` est minimale (pas d'utilisateur applicatif dédié en base, pas de restriction
  réseau) : le durcissement complet reste à l'étape 10.
- Les alertes ne sont pas encore branchées : un blocage ou une erreur persistante se voit
  aujourd'hui seulement dans les journaux (`log.critical`), pas encore sur Telegram ou par e-mail.
  C'est l'objet de l'étape 8.
- Le score à égalité observé reste un cas isolé (un seul sur l'ensemble des données vues à ce jour) :
  sa cause exacte (interruption, incident technique, ou règle rare) n'est pas établie.

### Étape 7 : résilience complète (2026-09-21) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `src/collector/transport/proxypool.py` | `ProxyPool` : rotation, retrait après échecs répétés, restauration automatique. Réalisé mais désactivé par défaut (décision D5) |
| `src/collector/sources/legacy/models.py`, `client.py`, `adapter.py` | Source de secours : `GetChampZip` + `GetGameZip` (deux appels, contre un seul en v3), traduits vers les mêmes modèles que la source principale |
| `scripts/check_legacy_live.py` | Vérification manuelle bornée de la source de secours contre le vrai site |
| `tests/unit/test_proxypool.py`, `test_legacy_adapter.py` | 15 tests supplémentaires |
| Pannes systématiques ajoutées à `test_http.py` et `test_results.py` | Délai réseau (timeout), JSON corrompu sur l'endpoint des résultats |
| Bascule automatique dans `scheduler.py` | Voir plus bas |

**Bascule automatique vers la source de secours.** Quand la source principale (v3) ne valide plus
son schéma (``ParserError`` — changement de structure du site), le cycle en cours n'est pas perdu :
la réponse brute est archivée dans `dead_letter`, et la ligue bascule immédiatement sur la source de
secours pour ce cycle et les suivants. Elle y reste, puis retente périodiquement la source
principale (toutes les 12 cycles par défaut, soit environ 1 min) pour détecter une réparation. Un
changement de structure **dégrade** la collecte (plus lente : N+1 requêtes au lieu d'une, marchés
identiques mais assemblés en deux temps), il ne l'arrête jamais — traitement bien distinct d'un
blocage (403/429), qui arrête tout.

**Une vraie erreur de formule trouvée en écrivant les tests, avant tout lancement contre une base
réelle** : le décodage du paramètre encodé de la source de secours pour les marchés « durée du
round » (``P = round × 100 + ligne / 100``) oubliait la multiplication par 100 sur le reste
(``line = P - round×100`` au lieu de ``(P - round×100) × 100``), sous-évaluant chaque ligne d'un
facteur 100 (17,5 devenait 0,175). Repérée en confrontant le résultat à un exemple réel documenté à
la reconnaissance (Memoire.md section 15) pendant la rédaction du test correspondant. Corrigée avant
qu'aucun test ne tourne dessus.

**Vérifications contre le site réel** : les 140 tests automatisés utilisent des réponses simulées
ou des échantillons déjà capturés ; en plus, un script dédié (`scripts/check_legacy_live.py`) a
interrogé la vraie source de secours (2026-09-21) : 5 matchs de Mortal Kombat X récupérés avec leurs
cotes (19 à 37 par match) en 4,2 s cumulées. Le format `GetChampZip`/`GetGameZip` et le décodage des
paramètres n'ont pas changé depuis la reconnaissance.

**Pannes couvertes** (voir Memoire.md section 12 et docs/architecture.md §8) : 429 et 403 (arrêt
total, jamais de contournement, étape 4) ; 500 et autres erreurs 5xx (reprise avec attente
exponentielle, étape 4) ; délai réseau dépassé (même traitement que 5xx, nouveau test cette étape) ;
JSON corrompu (source principale, dictionnaire, et désormais résultats, tous testés) ; changement de
structure du site (bascule automatique, cette étape) ; coupure réseau en plein rattrapage (reprise
exacte, étape 5). **Non applicable à ce site** : un jeton ou une session expirée, puisqu'aucune
authentification n'existe (Memoire.md section 4) — le cas le plus proche, un refus explicite du
site, est déjà couvert par la politique de blocage.

**Composants de l'architecture d'origine restés volontairement non implémentés, avec leur raison** :
- `SessionManager` : aucune session ni cookie n'est nécessaire pour ce site (confirmé dès la
  reconnaissance, section 4) ; en écrire un aurait été une coquille vide sans contenu réel à gérer.
- `BrowserProfileManager` : l'identification est un User-Agent fixe et unique
  (`OddsCollector/1.0`), déjà en place dans `HttpClient` depuis l'étape 4 ; il n'y a rien à faire
  tourner (décision explicite de l'utilisateur contre toute rotation, section 20).
- `ChallengeManager` : aucun défi anti-bot n'a jamais été observé ; l'interface reste vide.

**Résultat** : 140 tests passés, confirmés sur deux exécutions complètes indépendantes, plus une
vérification réussie de la source de secours contre le site réel.

**Points d'attention pour la suite** :
- Le `ProxyPool` n'est câblé nulle part dans `HttpClient` (aucun besoin réel constaté) : son usage
  réel demanderait un client HTTP par proxy actif, httpx liant un proxy à la construction du client
  et non requête par requête.
- La source de secours ne reconstitue pas le tableau des rounds (durée, type de finish) : en mode
  dégradé, seules les cotes et le score global restent à jour, dégradation jugée acceptable et
  documentée plutôt que cachée.
- Le seuil de 12 cycles avant un nouvel essai de la source principale est une valeur de départ,
  jamais mesurée en conditions réelles de panne prolongée.

### Étape 8 : observabilité et alertes (2026-09-21/22) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `src/collector/observability/metrics.py` | Métriques Prometheus (requêtes, latence, reprises, coupe-circuit, blocages, changements de schéma, cotes écrites, profondeur de la file, durée d'écriture, ancienneté du dernier cycle) |
| `src/collector/observability/logging_setup.py` | Journaux JSON structurés, avec masquage automatique des secrets (filet de sécurité) |
| `src/collector/alerting.py` | `AlertSender` (Telegram + e-mail, jamais bloquant), `ThrottledAlerter` (une alerte identique au plus toutes les 30 min) |
| `scripts/watchdog.py` | Surveillance externe (cron), indépendante du reste du code : détecte aussi un conteneur entièrement à l'arrêt |
| `monitoring/prometheus.yml`, `monitoring/grafana/provisioning/` | Configuration du scraping et tableau de bord Grafana de départ (7 panneaux) |
| Services `prometheus` et `grafana` dans `docker-compose.yml` | Grafana lié à 127.0.0.1 uniquement (jamais exposé sur Internet, tunnel SSH sur le VPS) |
| 30 tests supplémentaires | `test_alerting.py`, `test_logging_setup.py`, `test_metrics.py`, `test_watchdog.py`, et des ajouts à `test_scheduler.py` |

**Deux vrais défauts trouvés en écrivant le câblage, avant tout test** :
1. Les cotes de la source de secours (legacy) étaient marquées comme venant de la source
   principale dans `odds_snapshots.source`, faussant toute analyse future distinguant les deux
   sources. Corrigé : `CycleJob` porte désormais la source réellement utilisée, transmise jusqu'à
   l'écriture.
2. La table `collection_log`, conçue dès l'étape 3 pour distinguer « la cote n'a pas changé » de
   « le collecteur était aveugle » (indispensable avec l'option A), n'avait en réalité **jamais été
   alimentée**. Corrigée : chaque cycle, réussi ou en échec, y est désormais enregistré, avec la
   bonne source.

**Deux vrais défauts de déploiement trouvés en faisant tourner la vraie image du collecteur**
(et non plus seulement le conteneur de tests, qui monte tout le dépôt en volume et masque ce genre
de problème) :
3. **Le programme principal n'a jamais exécuté les migrations au démarrage.** La base persistante
   (volume Docker) était restée sur un schéma antérieur à la migration 005 (scores à égalité
   autorisés). Le tout premier match à égalité rencontré en conditions réelles pendant le
   rattrapage a fait planter le conteneur en boucle (`NotNullViolationError` sur `winner`).
   Corrigé : `main.py` applique désormais les migrations en attente avant tout accès à la base,
   comme le fait déjà l'exécuteur testé depuis l'étape 3.
4. **L'image de production ne copiait pas le dossier `migrations/`.** Une fois le défaut précédent
   corrigé, le conteneur plantait immédiatement avec `dossier de migrations introuvable`. Corrigé
   dans le `Dockerfile` (cible `runtime`).

**Vérification complète en conditions réelles (2026-09-22)**, au-delà des tests automatisés :
la vraie image reconstruite tourne de façon stable (`healthy`, aucun redémarrage) ; les migrations
s'appliquent automatiquement au démarrage ; `/metrics` répond avec les compteurs attendus ;
**Prometheus voit la cible comme `up`** ; **Grafana est en bonne santé, avec la source de données
et le tableau de bord provisionnés automatiquement** (vérifié via son API) ; **un message d'alerte
réel a été envoyé sur les deux canaux (Telegram et e-mail) à travers le code réellement déployé**
(pas un script à part), sans erreur journalisée — réception à confirmer par l'utilisateur, une
absence d'erreur ne garantissant pas la livraison.

**Ce que les tests automatisés prouvent** : un blocage, une bascule vers la source de secours, un
rétablissement, et un blocage du CDN déclenchent chacun exactement une alerte (pas de répétition à
chaque cycle) ; les canaux d'alerte échouent indépendamment l'un de l'autre sans jamais lever
d'exception ; la limitation par clé supprime les répétitions dans le délai puis les autorise à
nouveau après, et un « reset » explicite permet de resignaler immédiatement une rechute ; les
journaux sont un JSON valide avec les champs attendus, et les secrets connus y sont masqués ; le
script de surveillance externe détecte correctement un cycle en retard, un cycle jamais enregistré,
et n'alerte pas quand tout est frais ; un cycle réussi ou en échec est bien journalisé dans
`collection_log` avec la source exacte (secours jamais confondue avec la principale, verrouillé par
un test dédié).

**Résultat** : 171 tests passés (unitaires et intégration), confirmés sur deux exécutions complètes
indépendantes, plus la vérification en conditions réelles ci-dessus.

**Commandes** (nouvelles depuis l'étape 8) :

```text
docker compose up -d db scraper prometheus grafana   # pile complète avec observabilité
# Grafana : http://127.0.0.1:3000 (identifiant admin, mot de passe dans .env, GRAFANA_ADMIN_PASSWORD)
# sur le VPS : ssh -L 3000:localhost:3000 <utilisateur>@<vps>, puis la même adresse locale
python scripts/watchdog.py                            # surveillance externe, à mettre en cron (5 min)
```

**Points d'attention pour la suite** :
- Certaines métriques de la liste minimale d'origine ne sont pas mesurées, avec la raison
  documentée directement dans `metrics.py` (`authentication_failures` sans objet, `duplicates` non
  mesurable proprement avec `asyncpg`).
- Le tableau de bord Grafana est un point de départ (7 panneaux) : à enrichir à l'usage.
- Le script de surveillance externe n'a pas encore été installé en tâche planifiée sur un VPS réel
  (pas d'accès pour l'instant, Memoire.md section 13).
- La réception réelle du message d'alerte de vérification reste à confirmer par l'utilisateur.

### Étape 9 : tests systématiques et couverture (2026-09-22) : terminée

Confirmation de l'estimation faite à l'étape 8 : l'essentiel du travail avait déjà été fait au fil
des étapes précédentes. Cette étape a donc consisté à **mesurer objectivement** la couverture
(jusqu'ici jamais chiffrée, seulement estimée au jugé) puis à combler les manques réels qu'elle a
révélés, plutôt qu'à repartir de zéro.

**Outillage ajouté** : `pytest-cov` (`requirements-dev.txt`), avec une configuration dédiée dans
`pyproject.toml` (`[tool.coverage.run]` / `[tool.coverage.report]`) :
- couverture de branches activée (`branch = true`), plus stricte qu'un simple compte de lignes ;
- `src/collector/prototype.py` explicitement exclu (`omit`), avec la raison documentée dans le
  fichier lui-même : c'est un script de démonstration manuelle à un seul cycle, jamais utilisé en
  production (c'est `scheduler.py` qui orchestre réellement la collecte), et le compter aurait
  faussé le pourcentage global avec du code qui n'a pas vocation à être testé automatiquement.

**Mesure de départ** : 90 % (lignes) sur l'ensemble du code de production, 171 tests. Deux manques
significatifs identifiés par la mesure elle-même (pas par relecture manuelle) :
1. **`src/collector/main.py` (point d'entrée de production) : jamais testé**, alors que c'est
   précisément ce module qui contenait deux des quatre vrais défauts de déploiement trouvés à
   l'étape 8 (migrations jamais appliquées, dossier de migrations manquant). Corrigé par un nouveau
   `tests/unit/test_main.py` (5 tests) : ordre migrations-avant-connexion à la base verrouillé,
   code de sortie (0 normal / 3 si bloqué), fermeture propre de toutes les ressources même si
   l'ordonnanceur lève une exception, avertissement si aucun canal d'alerte n'est configuré.
2. **`src/collector/transport/ratelimit.py` (limiteur de débit et coupe-circuit) : seulement
   couvert indirectement**, via les tests du client HTTP. Corrigé par un nouveau
   `tests/unit/test_ratelimit.py` (9 tests rapides, sans réseau ni base) : espacement minimal réel
   entre requêtes (y compris en cas d'appels concurrents), et les trois états du coupe-circuit
   (fermé, ouvert, semi-ouvert) y compris la reprise complète après le délai de récupération.

**Autres manques comblés une fois la mesure faite** :
- `src/collector/storage/migrate.py` : l'enveloppe en ligne de commande `main()` (jamais testée)
  et le cas « dossier de migrations introuvable » (`discover()`) — précisément le message d'erreur
  qui a servi de seul indice au vrai défaut de déploiement de l'étape 8. Nouveau
  `tests/unit/test_migrate_cli.py` (4 tests) et un ajout à `tests/integration/test_migrations.py`.
  Couverture du module : 80 % → 100 %.
- `src/collector/scheduler.py` (le plus gros module, 244 lignes) : 7 tests supplémentaires dans
  `tests/integration/test_scheduler.py` couvrant des branches de résilience non encore exercées :
  blocage pendant le préchargement (`preload_all`), erreur transitoire sur une seule ligue au
  préchargement (les autres ne sont pas affectées), arrêt déjà demandé avant le début d'un
  rattrapage ou d'une réconciliation (aucun appel réseau inutile), reprise d'une tâche périodique
  après une exception transitoire, `write_once` sur une file vide, et une ligue déjà en mode
  dégradé dont la source de secours échoue à son tour (cycle perdu proprement, pas de blocage).
  Couverture du module : 86 % → 91 % (lignes), 91 % (branches).

**Résultat final, confirmé sur deux exécutions complètes indépendantes** : **198 tests, couverture
globale 96 % (couverture de branches)**, contre 90 % (lignes seules) en début d'étape. Le détail
par fichier est dans la sortie de `pytest --cov` (conservée en commentaire dans les scripts de
vérification) ; en résumé, les modules à 100 % couvrent l'essentiel de la logique métier
(`config`, `dedupe`, `normalize`, `pipeline`, `main`, `migrate`, la source legacy, `ratelimit`), et
les quelques pourcents restants ailleurs sont des branches à faible risque déjà identifiées comme
telles (garde `if __name__ == "__main__"`, cas rares de validation Pydantic, chemins d'erreur du
client CDN peu critiques) plutôt que des trous cachés.

**Ce que cette étape ne visait pas** : ajouter de nouveaux scénarios métier (déjà couverts en
profondeur depuis les étapes 3 à 8) ou viser 100 % partout — au-delà d'un certain point, forcer la
couverture de branches de garde très improbables aurait ajouté des tests fragiles sans valeur de
détection réelle, ce qui aurait été contraire à la discipline de ce projet (chaque test doit
correspondre à un vrai scénario de panne ou de comportement attendu, pas à une ligne à cocher).

**Commande** :

```text
docker compose run --rm tests pytest --cov=collector --cov-report=term-missing -q
```

### Étape 10 : endurcissement du déploiement (2026-09-22) : terminée

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `scripts/backup_db.sh` | Sauvegarde quotidienne (`pg_dump` compressé, horodaté, rotation locale configurable) |
| `scripts/restore_db.sh` | Restauration depuis une sauvegarde, avec confirmation explicite avant d'écraser une base non vide |
| `README.md` | Point d'entrée pour un tiers qui reprendrait le projet (démarrage rapide, tests, sauvegardes, surveillance, structure) |
| `.gitignore` (mis à jour) | Exclut désormais `.coverage`, `htmlcov/` et `/backups/` (données locales, jamais du code) |
| `docker-compose.yml` (mis à jour) | Journaux Docker bornés sur tous les services longue durée (`x-logging`, 10 × 10 Mo) ; service `tests` réutilisé (avec `env_file`) pour lancer le watchdog en tâche planifiée |

**Un vrai défaut trouvé en testant réellement la restauration** (et non en supposant que
`pg_dump`/`psql` suffit, ce qui aurait été l'erreur) : une sauvegarde produite normalement par
`pg_dump` échoue à se restaurer telle quelle sur une base TimescaleDB — erreur de contrainte de
clé étrangère sur les chunks internes d'hypertable (`_timescaledb_internal._hyper_1_1_chunk`), qui
ne sont pas encore repartitionnés au moment où `pg_dump` vérifie les contraintes lors de la
restauration. Corrigé en encadrant la restauration des deux fonctions officielles de TimescaleDB,
`SELECT timescaledb_pre_restore();` avant et `SELECT timescaledb_post_restore();` après — **vérifié
en conditions réelles** : sauvegarde de la base réelle (2,4 Mo), restauration dans une base
jetable, comptage des lignes après restauration (3336 lignes de cotes, 27 événements, conformes à
la base d'origine), puis suppression de la base jetable. Sans ce test réel, ce défaut ne serait
resté qu'une hypothèse de documentation, découvert bien plus tard, en conditions de panne réelle
sur le VPS — exactement le genre d'écart entre « ça devrait marcher » et « ça marche » que ce
projet cherche systématiquement à éliminer avant le déploiement.

**Décisions prises et documentées plutôt qu'un travail non fait en silence** :
- **Taille de l'image d'exécution (219 Mo)** : jugée déjà adéquate. Elle est construite en deux
  étapes depuis `python:3.12-slim` (déjà nettoyée de ses outils de compilation par l'image
  officielle elle-même) ; les seules dépendances de production (`asyncpg`, `httpx`, `pydantic`,
  `PyYAML`, `prometheus-client`) s'installent depuis des paquets binaires précompilés, sans
  compilateur nécessaire côté projet. Passer à une base `alpine` gagnerait quelques dizaines de Mo
  au prix d'un risque réel de fragilité de build (bibliothèques C liées à `glibc` absentes sous
  `musl`, recompilation potentiellement nécessaire) — un mauvais compromis sur un VPS de 50 Go où
  la taille de l'image n'est pas une contrainte.
- **Rôle de base de données** : `POSTGRES_USER` n'est déjà pas le superutilisateur `postgres` par
  défaut (c'est `collector`, avec un mot de passe généré), et cette base est isolée dans son propre
  conteneur, jamais partagée avec un autre service, jamais exposée en dehors du réseau Docker. Une
  séparation plus fine (un rôle distinct pour les migrations et un rôle distinct, moins privilégié,
  pour l'écriture courante) a été envisagée mais écartée pour l'instant : elle ajouterait de la
  complexité de configuration (deux chaînes de connexion à gérer dans un seul conteneur applicatif)
  pour un gain de sécurité marginal dans ce contexte à un seul service, une seule base, aucun accès
  multi-tenant. À reconsidérer si l'architecture évolue vers plusieurs services partageant la base.

**Autres vérifications en conditions réelles (2026-09-22)**, sur la vraie pile (pas seulement les
tests automatisés) : reconstruction de l'image `scraper`, redémarrage propre (`status=running`,
migrations « à jour, 5 appliquées », aucune erreur), confirmation via `docker inspect` que la
limite de journaux (`max-size: 10m, max-file: 10`) est bien appliquée au conteneur.

**Résultat** : 198 tests toujours au vert (aucune régression, ces changements ne touchent pas le
code applicatif testé), plus la vérification réelle de sauvegarde/restauration ci-dessus.

### Étape 11 : déploiement VPS réel (2026-09-22) : terminée

L'utilisateur a obtenu un accès AWS (compte gratuit, 100 $ de crédit) et créé le VPS le jour même.
Choix retenus (voir `docs/runbook.md`, nouvellement créé) : instance **m7i-flex.large** (2 vCPU,
8 Go — équivalent exact de la cible `t3.large` de l'architecture, `t3.large` n'étant pas proposée
par l'assistant de lancement simplifié sur ce compte), région **eu-west-3 (Paris)**, **Ubuntu
Server 24.04 LTS** (x86, non-Pro), 50 Go gp3, groupe de sécurité restreint au SSH depuis l'IP de
l'utilisateur (aucun autre port ouvert), IP publique automatique (Elastic IP non allouée par
l'utilisateur pour l'instant — à faire avant un fonctionnement prolongé, voir « points restés
ouverts » plus bas).

**Le test qui ne pouvait se faire qu'à ce moment précis, réalisé avec succès** : depuis la
véritable adresse IP du VPS (AWS, région Paris), le site `melbet-cm.com` répond normalement
(`HTTP/1.1 200 OK` sur tous les appels v3, aucun 403/429). Confirme ce que la reconnaissance
initiale laissait supposer sans certitude absolue (Memoire.md, section 4) : aucune protection
anti-bot basée sur la géographie ou la réputation d'IP n'affecte ce déploiement.

**Déploiement effectué** : Docker et le pare-feu applicatif (`ufw`, SSH uniquement) installés sur
le serveur, dépôt cloné dans `/opt/oddscollector`, pile complète (`db`, `scraper`, `prometheus`,
`grafana`) démarrée. Résultat, vérifié en conditions réelles : les 4 services `healthy`/`up`, les
5 migrations appliquées automatiquement au tout premier démarrage, Prometheus voit la cible
`up`, Grafana répond `healthy`. Tâches planifiées installées (`crontab`) : sauvegarde quotidienne
à 3h et surveillance externe toutes les 5 min, toutes deux vérifiées manuellement avec succès.

**Un vrai défaut trouvé en déployant réellement** (et non en écrivant seulement le runbook) : le
`.env` local a été copié vers le VPS par commodité (`scp`) plutôt que ressaisi à la main — or ce
fichier, édité sous Windows, a des fins de ligne CRLF. `scripts/backup_db.sh` et
`scripts/restore_db.sh` font tous les deux un `source .env` classique en bash, qui laisse un `\r`
invisible collé à la fin de chaque valeur une fois sourcé sous Linux (`POSTGRES_USER` devenant de
fait `"collector\r"`, rejeté par PostgreSQL comme un rôle inexistant — repéré en investiguant un
faux signal du watchdog). **Le conteneur `scraper` lui-même n'était pas affecté** : l'analyseur
`env_file` de Docker Compose normalise déjà correctement les fins de ligne, vérifié explicitement
en interrogeant les variables d'environnement du conteneur sans jamais afficher leur valeur.
Corrigé dans les deux scripts (`tr -d '\r'` avant de sourcer) et documenté dans le runbook comme
piège à éviter après un `scp` depuis Windows. Un `.gitattributes` a aussi été ajouté pour garantir
que les scripts `.sh` restent en LF dans le dépôt (déjà le cas, vérifié, mais désormais garanti
explicitement plutôt que dépendant de la configuration git locale de chaque contributeur).

**Un faux signal, pas un défaut** : un premier essai manuel du watchdog, environ deux minutes
après le tout premier démarrage, a signalé un retard — en réalité le tout premier cycle réussi
n'avait pas encore eu le temps d'être journalisé pour les deux ligues. Confirmé en interrogeant
`collection_log` directement (27 cycles réussis par ligue quelques minutes plus tard) puis en
relançant le watchdog, qui est passé au vert.

**Points restés ouverts, à traiter avant un fonctionnement prolongé sans surveillance** :
- **IP Elastic non allouée** : l'IP publique actuelle changerait si l'instance était redémarrée.
  À faire dès que possible (voir `docs/runbook.md` §3).
- **Budget AWS** : l'alerte de suivi de coût (`docs/runbook.md` §8) n'a pas encore été confirmée
  comme configurée par l'utilisateur.
- **Réception réelle des alertes** (Telegram/e-mail) depuis ce nouveau déploiement : pas encore
  testée explicitement depuis le VPS (l'a été depuis la machine locale à l'étape 8) — la
  configuration est identique (même `.env`), risque jugé faible, mais à confirmer.
- Les récupérations régulières de sauvegardes vers une machine hors VPS (recommandé hebdomadaire)
  n'ont pas encore commencé.

**Résultat** : les 11 étapes du plan de développement initial sont désormais toutes terminées.
Le collecteur tourne en production réelle, sur le vrai VPS, contre le vrai site, avec observabilité
et alerting branchés.

---

Statut : reconnaissance terminée, architecture validée, **étapes 3 à 11 terminées et déployées en production sur le VPS AWS (254 tests, couverture 95 % en branches, vérifications réelles à chaque étape)**. Le collecteur s'identifie honnêtement, ne contourne jamais un blocage, bascule automatiquement sur une source de secours, alerte réellement par Telegram et e-mail, applique ses propres migrations au démarrage, journalise sans croissance illimitée, sauvegarde quotidiennement et est surveillé en externe toutes les 5 min. **Fil de match Telegram en direct, déployé et vérifié en production** : un salon dédié par ligue, numéro de match du jour calculé, format à emojis, annonce pré-match avec portraits et compte à rebours édité toutes les ~10 s, transition automatique vers le suivi manche par manche au démarrage. **Plan d'entraînement de modèles prédictifs livré** (docs/forecasting/plan_entrainement_mortal_kombat.docx). Décisions : option A ; rétention indéfinie ; deux ligues (Mortal Kombat X et Mortal Kombat 3) ; alerting e-mail et Telegram (branchés, vérifiés) ; fil de match Telegram sur un salon dédié par ligue, déployé en production ; sauvegardes quotidiennes sur le VPS et récupération par l'utilisateur.

## 25. Clôture de session — 2026-09-23

**Fait aujourd'hui** (à la suite de l'étape 11 déployée la veille) :
- **Fil de match Telegram en direct** : conçu, implémenté, testé (34 tests) et déployé — vainqueur,
  temps et type de finish par manche, un message édité par match. Commit `b08f0e8`.
- **Quatre ajustements demandés après coup**, tous implémentés et vérifiés en conditions réelles
  avant déploiement : salon Telegram séparé par ligue (l'utilisateur a créé un second groupe),
  numéro de match du jour corrigé (un vrai problème de méthode trouvé et corrigé : le champ `num`
  du site n'est pas ce numéro, calcul propre basé sur `results`+`events`), nouveau format à emojis
  exact, et annonce pré-match avec portraits des combattants (49 images fournies par l'utilisateur)
  et compte à rebours réutilisant celui du site. Commit `f573f7c`.
- **Incident opérationnel résolu** : le VPS est devenu injoignable en SSH en cours de session — pas
  un problème d'identifiants mais le groupe de sécurité, qui n'autorisait que l'ancienne IP
  détectée à la création. Diagnostiqué (IP sortante de cette session identifiée), corrigé par
  l'utilisateur dans la console AWS, puis déploiement complet effectué avec succès, vérifié en
  conditions réelles sur le VPS lui-même (pas seulement en local).
- **Plan d'entraînement de modèles prédictifs** livré à la demande de l'utilisateur : un document
  Word complet (12 sections) pour deux modèles distincts — durée de manche (Mortal Kombat X,
  probabilité ancrée sur la cote du marché) et type de finish (Mortal Kombat 3, 7 classes
  déséquilibrées). Commit `93811e8`.
- Tout est testé (254 tests, deux exécutions stables au fil de la session), documenté, et poussé
  sur `github.com/jeremie-ndjock/melbet-scrapper`.

**État en fin de session** : le collecteur tourne en production réelle et complète sur le VPS —
cotes, résultats, alertes, sauvegardes, surveillance externe, et désormais le fil de match Telegram
en direct avec portraits. Les 11 étapes du plan initial et les demandes additionnelles du jour sont
toutes closes.

**Points restés ouverts, jamais retranchés** :
- IP Elastic toujours non allouée sur le VPS (`docs/runbook.md`, §3) — l'adresse changerait si
  l'instance redémarrait.
- Alerte de budget AWS non confirmée comme configurée.
- Réception effective, par l'utilisateur, de l'alerte de test envoyée à l'étape 8 jamais
  explicitement reconfirmée (sans conséquence : les vraies alertes de match, elles, sont
  confirmées reçues).
- Le plan d'entraînement de modèles n'est qu'un plan pour l'instant : aucune extraction ni
  entraînement réel n'a encore été fait (prochaine étape naturelle si l'utilisateur souhaite
  avancer dessus).

## 22. Clôture de session — 2026-09-22

Session close sur les **étapes 9 et 10**, à la suite des étapes 3 à 8 déjà closes précédemment.

**Fait aujourd'hui** :
- Étape 9 (tests systématiques et couverture) : mesure objective de la couverture pour la première
  fois (`pytest-cov`), deux vrais manques trouvés par la mesure (`main.py` jamais testé,
  `ratelimit.py` couvert seulement en indirect), comblés avec 27 nouveaux tests ciblés. Résultat :
  171 → 198 tests, couverture 90 % (lignes) → 96 % (branches). Commit `6449aad`.
- Étape 10 (endurcissement du déploiement) : scripts de sauvegarde/restauration créés **et
  vérifiés en conditions réelles**, ce qui a révélé un vrai défaut (une sauvegarde `pg_dump`
  classique de cette base ne se restaure pas telle quelle sur TimescaleDB, corrigé avec les
  fonctions officielles `timescaledb_pre_restore()`/`timescaledb_post_restore()`) ; journaux Docker
  bornés (10 × 10 Mo) pour un service qui tourne 24/7 ; `README.md` créé pour un tiers qui
  reprendrait le projet ; décisions sur la taille d'image et la séparation des rôles de base
  documentées plutôt que traitées en silence. Commit `0a458b3`.
- Tout est poussé sur `github.com/jeremie-ndjock/melbet-scrapper` (branche `main`, à jour).
- Aucun secret vérifié absent des fichiers versionnés à chaque étape (grep systématique avant
  commit).

**État en fin de session** : étapes 3 à 10 du plan original **toutes terminées et testées**. Seule
l'étape 11 (déploiement VPS réel) reste, et elle est bloquée sur un élément externe : **l'accès au
VPS**, toujours non disponible côté utilisateur à la date de clôture (voir section 13). Rien
d'autre n'est en attente de décision de la part de l'utilisateur pour l'instant.

**À la prochaine session** :
1. Si le VPS est devenu accessible : démarrer l'étape 11 (runbook de mise en service, déploiement
   réel, et surtout le test qui ne peut se faire qu'à ce moment-là — le comportement du site
   depuis la véritable adresse IP du VPS).
2. Sinon : le projet est dans un état stable et complet pour un développement local ; aucune
   action urgente n'est requise avant l'accès au VPS.
3. Point resté ouvert et jamais retranché de la liste des sujets en suspens (section 13/21) : la
   réception réelle de l'alerte de test envoyée à l'étape 8 n'a jamais été explicitement confirmée
   par l'utilisateur — à reconfirmer si l'occasion se présente, sans bloquer la suite.

## 23. Fil de match Telegram en direct (2026-09-22)

Fonctionnalité demandée par l'utilisateur après la clôture de l'étape 11, hors du plan initial à
11 étapes : diffuser les matchs en direct sur Telegram, avec mise à jour automatique à chaque
manche (vainqueur, temps, type de finishing), sur le modèle :

```
Goro VS Ermac
Manche 1 : vainqueur Goro, temps: 31 secondes, Type de finishing: Regular
```

**Découverte clé, avant tout code** : la donnée nécessaire existait déjà dans les notes de
reconnaissance (section 15, « Tableau des rounds ») mais n'avait **jamais été implémentée** :
l'endpoint `GET /cyber-api/mainfeedlive/web/cyber/v3/statistic?fcountry=84&gameId={id}&gr=2147&lng=fr&ref=8`
renvoie `statistic.main.RoundTable`, une **chaîne** JSON (à décoder une seconde fois) donnant, pour
chaque manche déjà terminée : `R` (numéro), `T` (durée en secondes), `W` (nom du vainqueur, en
toutes lettres), `DI` (type de finishing : Regular/Fatality/Brutality/...), `WT` et `FW`. Se met à
jour manche par manche, en direct — contrairement aux résultats officiels (`results.py`), qui
n'arrivent qu'après la fin complète du match. Vérifié en conditions réelles le 2026-09-22 sur trois
matchs (en cours, tout juste commencé, terminé) : toujours `200`, jamais le `204` documenté en
reconnaissance (qui correspond à un match déjà disparu de la liste, donc jamais interrogé par ce
collecteur).

**Décisions prises avec l'utilisateur** : un salon Telegram dédié (groupe « Ligue Mortal Kombat »,
distinct du chat d'alertes techniques existant, pour ne jamais mélanger un flux fréquent avec des
alertes rares et critiques) ; **un seul message par match, édité à chaque manche** (pas un nouveau
message à chaque fois) ; le score courant affiché à chaque ligne.

**Fichiers créés** :

| Fichier | Rôle |
|---|---|
| `migrations/006_match_feed.sql` | Table `match_feed` : un message Telegram par match, son identifiant (pour l'éditer), le nombre de manches déjà publiées, l'état terminé |
| `src/collector/sources/v3/statistic.py` | Appel et décodage de `v3/statistic` (tolérant : une manche illisible est ignorée, jamais tout le tableau) |
| `src/collector/telegram_feed.py` | `MatchFeedSender` (envoi/édition Telegram, jamais bloquant), `format_match_message` (reconstruit le texte en entier à chaque fois — idempotent, aucune dérive possible) |
| `src/collector/live_feed.py` | `LiveFeedProcessor` : détecte une nouvelle manche (score qui augmente), appelle `v3/statistic` seulement à ce moment-là (pas à chaque cycle de 5 s, pour rester dans le débit alloué), publie/édite le message, et **complète au passage `round_results`** (durée, type de finish) — des colonnes prévues dès la migration 002 mais jamais alimentées avant cette fonctionnalité |
| 34 tests supplémentaires | `test_statistic.py`, `test_telegram_feed.py`, `test_live_feed.py`, et des ajouts à `test_scheduler.py` |

**Un vrai défaut de logique trouvé en relisant le code avant de lancer les tests** : la condition
initiale pour savoir si un match avait « du nouveau » à publier ne gérait pas correctement le cas
où le libellé « Jeu terminé » arrive un cycle après que le score a atteint son total final
(comportement déjà documenté section 4) — le drapeau `match_finished` serait resté bloqué à faux
indéfiniment dans ce cas. Corrigé avant tout test, avec un test dédié qui simule explicitement ce
décalage d'un cycle.

**Politique de blocage respectée** : un blocage sur l'endpoint `statistic` se propage et arrête
tout l'ordonnanceur, exactement comme un blocage sur les cotes — jamais de contournement, même
pour cette fonctionnalité annexe. Une erreur transitoire ou de schéma, elle, n'affecte que ce match
pour ce cycle (le suivant réessaiera), sans jamais interrompre la collecte des cotes elle-même.

**Vérification en conditions réelles (2026-09-22)**, au-delà des 34 nouveaux tests (231 au total,
confirmés sur deux exécutions indépendantes, couverture globale 96 %) : un vrai message a été
envoyé sur le vrai groupe Telegram à partir des vraies données d'un match réellement en cours
(Jacqueline Briggs vs Ferra & Torr, 5 manches, score 2-3), avec le format exact demandé ; son
édition a ensuite été testée réellement avec succès (le message affiché a bien changé sur place,
confirmé par l'utilisateur).

**Portée volontairement limitée** : le fil de match n'est câblé que sur la source principale (v3)
et la source de secours (legacy, via l'adaptateur commun) au même titre que les cotes — un
changement de structure spécifique à l'endroit `statistic` n'entraîne pas de bascule dédiée (juste
une manche non publiée ce cycle, réessayée au suivant). Fonctionnalité optionnelle et désactivée
par défaut : sans `TELEGRAM_MATCH_CHAT_ID` dans `.env`, rien ne change au fonctionnement existant.

**Déployé et vérifié en production sur le VPS le jour même** : `TELEGRAM_MATCH_CHAT_ID` ajouté au
`.env` du VPS, code redéployé, confirmé par les journaux réels du conteneur (appels `v3/statistic`
puis envois Telegram réussis) et par l'utilisateur, qui a vu les mises à jour arriver dans le
groupe. 4 matchs suivis simultanément dès le premier cycle après déploiement.

### Complément demandé le même jour : distinguer la ligue et le numéro du match

L'utilisateur a ensuite demandé que chaque message indique la ligue (Mortal Kombat X ou Mortal
Kombat 3) et le numéro du match dans la journée.

**Un vrai piège évité avant de coder** : le champ `num` déjà présent dans les réponses du site
(ex. `220241`) ressemble à un numéro de match mais n'en est pas un — vérifié en conditions réelles
sur les 7 matchs en direct au moment du test : valeurs sans aucune corrélation avec l'ordre ou
l'heure de début. C'est un identifiant interne du bookmaker, pas un compteur journalier. Le numéro
affiché est donc **calculé par le collecteur lui-même** : le rang du match parmi tous ceux de la
même ligue commençant le même jour (UTC), par heure de début croissante (requête sur la table
`events`, déjà alimentée pour tous les matchs vus). Figé au premier calcul (nouvelle colonne
`match_feed.match_no_of_day`, migration 007) pour ne jamais changer une fois publié.

Format retenu :
```
🎮 Mortal Kombat X — Match n°12 de la journée
🥊 Leatherface VS Jason Voorhees

Manche 1 : vainqueur Leatherface, temps: 39 secondes, Type de finishing: Fatality (score 1-0)
...
```

**Fichiers modifiés** : `migrations/007_match_feed_match_number.sql` (nouvelle colonne, la
migration 006 étant déjà appliquée en production, jamais modifiée après coup) ;
`telegram_feed.format_match_message` (en-tête ligue + numéro) ; `live_feed.py` (calcul du rang,
transmission de la ligue) ; `scheduler.py` (transmet `league_id`/`league_name`, déjà connus de
l'ordonnanceur). 234 tests au total (+3), couverture 96 % maintenue.

**Vérifié en conditions réelles** : un vrai message envoyé pour chacune des deux ligues, à partir
de vrais matchs en cours, avec le format ci-dessus. Déployé sur le VPS le jour même : migration
007 appliquée automatiquement, service resté sain, `editMessageText` réussi observé dans les
journaux dès le premier cycle.

**Détail transitoire observé, sans conséquence** : les matchs déjà suivis par `match_feed` avant
ce déploiement (créés par la version précédente du code, sans la colonne `match_no_of_day`) n'ont
et n'auront jamais de numéro affiché pour ce match précis — `format_match_message` omet
simplement la ligne « Match n°X » quand la valeur est absente (jamais de plantage, jamais de
« None » affiché). Se résorbe de lui-même : tout match qui démarre après ce déploiement obtient
son numéro normalement, comme vérifié manuellement.

### Complément demandé le même jour : salons séparés, numéro exact, format à emojis, annonce pré-match avec portraits

L'utilisateur a fourni une capture montrant le vrai numéro de match affiché par le site (232, 233,
234) et demandé quatre choses : un salon Telegram séparé par ligue, le vrai numéro de rang du jour
(pas une approximation), un format de message précis (emojis donnés en exemple), et une annonce
avant chaque match avec les portraits des deux combattants et un compte à rebours.

**Un vrai problème de méthode trouvé avant de coder** : le numéro affiché par le site (232-234)
n'existe dans **aucun champ** de l'API interrogée — vérifié en listant tous les entiers de 0 à
5000 présents dans une vraie réponse `gamesByChamp` sur des matchs en direct, sans résultat
plausible. Le calcul déjà en place (rang parmi les matchs de la ligue commençant le même jour,
voir plus haut) restait donc la seule option, mais avec un vrai défaut : il ne comptait que les
matchs vus par ce collecteur depuis son démarrage, pas depuis minuit — sous-évalué juste après un
déploiement en cours de journée. **Corrigé** en combinant `results` (rattrapage historique,
couvre toute la journée même avant le premier démarrage) et `events` (matchs en direct) dans le
calcul (`UNION`, requête `COUNT_LEAGUE_MATCHES_UP_TO` mise à jour) : le numéro se rapproche
désormais de celui du site dès le premier jour de fonctionnement continu, et devient exact à
partir du deuxième jour (le premier restera légèrement sous-évalué pour les toutes premières
heures suivant un déploiement, ce qui est accepté).

**Salons séparés par ligue** : l'utilisateur a renommé le groupe existant en « Mortal Kombat X »
et créé un nouveau groupe « Mortal Kombat 3 ». `MatchFeedConfig.chat_ids` devient un dictionnaire
`{league_id: chat_id}` lu depuis des variables `TELEGRAM_MATCH_CHAT_ID_<league_id>` (une ligue
sans variable définie n'a simplement pas son fil, les autres continuent). `MatchFeedSender` ne
fixe plus le salon à la construction : chaque appel (`send`, `edit`, `send_or_edit`) le reçoit en
paramètre, un seul envoyeur suffisant pour toutes les ligues.

**Nouveau format exact** (emojis fournis par l'utilisateur) :
```
🎮 MORTAL KOMBAT 3
📅 Match n°27 — Journée du 22-09-2026
🥊 Liu Kang VS Kung Lao

💥 Manche 1 : Vainqueur Liu Kang — ⏱️ 22s — 💀 Fatality [Score : 1-0]
...
🏆 VAINQUEUR DU MATCH : Liu Kang (5-1)
```
Emoji par type de finishing (Regular 🥊, Fatality 💀, Brutality 🔥, Babality 👶, Friendship 🤝,
Animality 🐾, Hara-Kiri 🗡️) ; un type inconnu affiche ❓ plutôt que de planter.

**Annonce pré-match avec portraits** (fonctionnalité neuve, `pre_match.py`, `fighter_images.py`) :
- L'utilisateur a fourni 49 images (une par combattant distinct des deux ligues sur 1 145 matchs
  réels analysés — 33 pour Mortal Kombat X, 16 supplémentaires propres à Mortal Kombat 3, les 16
  autres étant des combattants communs aux deux jeux). Stockées dans `assets/fighters/`, indexées
  par le nom exact renvoyé par le site (`_mapping.json`), embarquées dans l'image Docker.
- **Piège d'encodage réel rencontré en les enregistrant** (déjà documenté section 20 du journal
  d'étape 11 pour un autre fichier) : une commande shell avec un nom accentué (« Prédateur ») a
  affiché un caractère corrompu à l'écran — vérifié que le fichier JSON lui-même restait correct
  en UTF-8 (lecture des octets bruts), donc pas de vrai bug, seulement un problème d'affichage
  terminal. Un test dédié (`test_accented_fighter_names_round_trip_correctly`) verrouille ce point.
- **Découverte qui a évité un calcul superflu** : le compte à rebours avant un match n'a pas besoin
  d'être recalculé depuis `startTs` — le site l'expose déjà lui-même
  (`scores.timer.timeSec`/`timeDirection == -1`), dans le relevé `gamesByChamp` déjà récupéré à
  chaque cycle. Réutilisé tel quel : plus fiable (fait foi côté site) et sans appel réseau
  supplémentaire. Champ `timeDirection` ajouté au modèle `Timer` (jamais déclaré avant).
- Un message par match, distinct du fil manche par manche (deux messages séparés, pas le même
  message qui se transforme) : portraits des deux combattants en album (`sendMediaGroup`) avec
  légende « Commence dans MM:SS », éditée au plus une fois toutes les 10 s (demandé explicitement),
  jusqu'au démarrage du match (dernière édition : « Le match commence ! »). Un combattant sans
  image connue ne bloque rien : retombe sur une seule photo, ou sur du texte si aucune des deux
  images n'existe.
- Nouvelle table `match_announcements` (migration 008), distincte de `match_feed` : suit le
  message d'annonce (identifiant, si c'était une photo ou du texte simple — `editMessageCaption`
  et `editMessageText` ne sont pas interchangeables côté Telegram —, dernier temps affiché, état
  terminé).

**Résultat** : 254 tests au total (+20), couverture 95 % maintenue. Vérifié en conditions réelles :
un vrai message de résultat envoyé sur chacun des deux nouveaux salons séparés (numéro de test
provisoire, le vrai numéro nécessitant un peu d'historique accumulé) ; une vraie annonce pré-match
envoyée avec les vrais portraits (« images: True True » confirmé) sur un vrai match à venir de
chaque ligue.

**En attente à la clôture de cette session** : le VPS est devenu injoignable en SSH (timeout au
niveau TCP, pas un problème d'identifiants) pendant cette session de travail — à vérifier côté
utilisateur dans la console AWS (état de l'instance, groupe de sécurité). Le code est prêt, testé
et poussé sur GitHub ; le déploiement sur le VPS reste à faire dès que l'accès est rétabli.

**Résolu** : le blocage venait du groupe de sécurité, qui n'autorisait le SSH que depuis l'IP
détectée à la création (celle du navigateur de l'utilisateur à ce moment-là), pas celle de cet
environnement d'exécution. Une fois la règle élargie par l'utilisateur, déploiement effectué avec
succès : migration 008 appliquée automatiquement, service resté sain, et confirmation en
conditions réelles sur le VPS que le cycle complet fonctionne — annonce pré-match avec portraits
(`sendMediaGroup`), compte à rebours édité toutes les ~10 s (`editMessageCaption`, 20 appels
observés en 2 min sur deux matchs simultanés), transition automatique vers le fil manche par
manche au démarrage (`match_no_of_day` correctement calculé, valeurs 263 et 265 observées —
bien plus réalistes que les premiers essais locaux, qui manquaient d'historique accumulé).

## 24. Plan d'entraînement de modèles prédictifs (2026-09-23)

À la demande de l'utilisateur, exploration de l'usage des données collectées pour du forecasting :
un modèle Mortal Kombat X (probabilité que la durée de la manche en cours dépasse un seuil,
ancrée sur la ligne du marché « Durée du Round », groupe 1074) et un modèle Mortal Kombat 3 (type
de finish de la manche en cours, cible à 7 classes fortement déséquilibrées — Hara-Kiri à 0,1 %
identifié comme le vrai facteur limitant du calendrier de collecte, ≈7 semaines pour seulement 100
exemples).

Document complet livré : **`docs/forecasting/plan_entrainement_mortal_kombat.docx`** (généré via
`docs/forecasting/generate.js`, bibliothèque `docx` — script conservé pour pouvoir régénérer ou
faire évoluer le document plus tard). Couvre : définition précise des cibles, ingénierie des
variables (avec la règle anti-fuite causale, section 4.4 du document), volume de données
nécessaire chiffré selon la granularité visée, requêtes SQL d'extraction, choix de modèles,
métriques d'évaluation (comparées à la cote du marché, jamais un chiffre absolu isolé), protocole
de validation walk-forward, calendrier en 5 phases, risques et limites assumés dès le départ
(nature simulée du jeu, plancher d'aléa irréductible).

Validé par vérification XSD (`scripts/office/validate.py` du kit `docx` : « All validations
PASSED! ») faute de LibreOffice disponible sur cette machine pour un rendu visuel complet — un
défaut réel de bordure de paragraphe a été trouvé et corrigé à cette occasion (docx-js sérialise
les bordures de paragraphe dans un ordre non conforme au schéma OOXML quel que soit l'ordre fourni
en entrée ; contournement : fond grisé sans bordure pour les blocs de code du document).

## 26. Exploration du catalogue esports complet de MelBet (2026-09-23)

À la demande de l'utilisateur, exploration des trois onglets de `/fr/esports/` pour identifier
d'autres cybersports à fort potentiel de forecasting, au-delà des deux ligues Mortal Kombat déjà
suivies. Playwright était de nouveau indisponible au départ (timeout de connexion) ; exploration
menée d'abord via les mêmes API déjà utilisées pour Mortal Kombat (identification honnête,
`OddsCollector/1.0`, ~1 requête/seconde), puis confirmée visuellement une fois Playwright
reconnecté en cours de session.

### Onglet « Réel » — tournois humains authentiques

`sportId=40` (« E-sport »), 56 ligues actives sur 24 h : **CS 2, Dota 2, League of Legends,
Valorant, Mobile Legends, Rainbow Six, Standoff 2, Deadlock, StarCraft II, Heroes of Might and
Magic III, Crossfire** — vrais joueurs, vraies équipes (PGL Wallachia, ESL Challenger League,
EMEA Masters...). Volume par ligue nettement plus faible que le virtuel (1 à ~40 matchs/24h par
ligue, contre des centaines pour un sport virtuel) puisque ce sont de vrais calendriers de
compétition, pas des générateurs 24/7. Nature du problème radicalement différente : la prédiction
dépendrait de la forme réelle des équipes/joueurs, pas d'un algorithme caractérisable
statistiquement — évalué comme un projet distinct, pas une extension du travail Mortal Kombat
(modalités proposées à l'utilisateur, voir plus bas).

### Onglet « Virtuel » — confirmation et enrichissement du rapport précédent

98 catégories de sports virtuels au total (`GET .../v3/leftmenu/virtual`). Classement par volume
mesuré sur 24 h (`GET /service-api/result/web/api/v2/champs`, params `country`/`partner`
requis en plus de `fcountry`/`gr` pour cet endpoint précis — sinon 400) :

| Rang | Sport | Volume/24h | Évaluation |
|---|---|---|---|
| 1 | FIFA (`sportId=85`) | 3 407 | Buts = événements discrets, littérature de forecasting football mature (Poisson) |
| 2 | Mortal Kombat, famille complète (`sportId=103`) | **1 782** (dont seulement 570 pour les 2 ligues déjà suivies) | **Extension directe et immédiate** : MK1, MK11, MK11 (BO3), MK11 XXL découverts en plus de MKX/MK3 — même moteur probable, pipeline déjà prêt |
| — | WWE 2K (`sportId=101`) | 914 | Issues discrètes (tombé/soumission/décompte) à vérifier |
| — | NBA 2K (`sportId=91`) | 595 | Points/quarts-temps, forecasting basket mature |
| — | TEKKEN 8 (`sportId=145`) | 574 | Même genre que MK (rounds, personnages nommés) |
| — | NHL (`sportId=89`) | 497 | Buts/périodes |
| — | PES (`sportId=144`) | 488 | Même famille que FIFA |
| — | UFC (`sportId=90`) | 367 | Structure quasi identique à MK (KO/soumission/décision par round) |

**Exclus explicitement, aucun potentiel** : tous les jeux de casino virtuel (Baccara, Roulette,
Dés, Durak, Seka, 21/Blackjack, Poker, Higher vs Lower, Crystal, Victory Formula, et tout ce qui
est littéralement nommé « Random Match »/« Random Battle ») — génération purement aléatoire,
aucune identité d'équipe/joueur exploitable, jusqu'à 2 880 matchs/24h pour certains (Texas
Hold'em/IndianPoker) mais sans aucun objet pour du forecasting.

### Onglet « MelCyber » (stream) — pas un onglet esport à part entière

Contrairement à ce que le nom suggérait, majoritairement du live-streaming de **vrais matchs de
sports réels** (`sportId` standards : 1=football, 3=basketball, 4=tennis, 2=hockey sur glace).
Deux exceptions simulées trouvées dedans, hors de leur place logique (onglet « Virtuel ») :
- **AI Table Tennis** (2 salles, Prague et Goa, `sportId=10`) — 134 matchs/24h **par salle**,
  soit 268/24h cumulé. Voir section 27, reconnaissance approfondie démarrée le jour même.
- **Virtual Kabaddi** (Quantum, PKL) — 21 matchs/24h, volume jugé trop faible pour l'instant.

### Modalités proposées pour un projet distinct « E-sport réel »

Si l'utilisateur souhaite un jour explorer le forecasting sur les compétitions e-sport réelles
(CS 2, Dota 2, LoL...), les conditions suivantes ont été posées avant tout démarrage :
1. **Autorisation contractuelle distincte à confirmer** : l'autorisation actuelle porte sur
   MelBet Cameroun / les ligues virtuelles Mortal Kombat (voir l'en-tête de ce document) ; un
   nouveau périmètre (même sur le même site, onglet différent) doit être explicitement confirmé
   par l'utilisateur avant toute collecte, au même titre que l'a été le choix des deux ligues MK.
2. **Mêmes règles de conduite non négociables** que pour tout le reste du projet (section 20) :
   identification honnête et fixe, aucune évasion anti-bot, un blocage est un signal d'arrêt,
   jamais un obstacle à contourner.
3. **Seules des données publiques de compétition** (résultats, calendriers, statistiques
   officielles) — jamais de données personnelles sur les joueurs au-delà de ce qui est déjà
   public dans le cadre professionnel (identique au traitement des noms de combattants MK).
4. **Sources tierces potentiellement nécessaires** (ex. classements/statistiques HLTV,
   Liquipedia, pour contextualiser la forme des équipes) : chacune a ses propres conditions
   d'utilisation, à vérifier et respecter séparément — jamais supposées couvertes par
   l'autorisation MelBet.
5. **Attentes de volume et de délai réalistes, posées dès le départ** : contrairement au virtuel
   (des centaines de matchs/jour, 24/7), le e-sport réel suit un vrai calendrier de compétition —
   des semaines, voire des mois, peuvent être nécessaires pour accumuler un historique exploitable
   par équipe/matchup, largement au-delà des délais mesurés pour Mortal Kombat (section 5 du plan
   d'entraînement).
6. **Vigilance renforcée sur l'intégrité des compétitions** : contrairement à un générateur
   virtuel, un vrai résultat sportif peut en théorie être affecté par des enjeux extra-sportifs
   (paris truqués, etc.) — utiliser uniquement des sources de données officielles et reconnues,
   ne jamais bâtir sur une source dont la fiabilité n'est pas établie.

## 27. Reconnaissance approfondie : AI Table Tennis (2026-09-23)

Démarrée le jour même à la demande de l'utilisateur, sur les deux salles trouvées section 26
(`champId` 3066896 « Prague », 3066897 « Goa », `sportId=10`).

**Infrastructure confirmée identique à Mortal Kombat** — même famille d'API
(`cyber-api/mainfeedlive/web/cyber/v3/gamesByChamp`, `service-api/result/web/api/v3/games`),
mêmes paramètres (`fcountry`, `gr`, `lng`, `ref`, tri alphabétique). Avantage concret : le
transport, la résilience (coupe-circuit, source de secours), et une bonne partie de la structure
du collecteur existant seraient réutilisables presque tels quels.

**Différences structurelles réelles, à traiter avant tout code** :
- **Noms de joueurs réels et reconnaissables** utilisés comme adversaires (ex. Alexis Lebrun,
  joueur français classé au niveau mondial ; Manav Thakkar, joueur indien de haut niveau) — très
  probablement une simulation dont les probabilités sont pondérées par un classement réel (comme
  FIFA utilise de vrais noms de club), ce qui rendrait l'identité du joueur un signal appris
  d'autant plus pertinent (à vérifier empiriquement : les taux de victoire simulés corrèlent-ils
  avec les classements ITTF réels ?).
- **Format de match fixe** : `matchInfoObj.matchFormat = "3 sets Match"` (au meilleur des 3 sets),
  cadence mesurée ≈ 1 match toutes les 10-11 minutes par salle (134 matchs/24h).
- **Les marchés ne sont PAS au niveau du match mais au niveau de chaque set** : contrairement à
  Mortal Kombat où `eventGroups` est directement sur le match, ici il est **vide au niveau
  racine** et les cotes vivent dans `subGamesForMainGame[].eventGroups` (un sous-objet par set,
  ex. `subGameName: "3 Set"`) — une vraie différence d'architecture qui demanderait un nouveau
  parseur (traversée des sous-matchs), pas une réutilisation directe de `normalize.py`.
- **Score officiel déjà propre et facile à analyser** :
  `service-api/result/web/api/v3/games` renvoie directement `"score": "2:0 (11:7,11:7)"` — score
  final en sets puis score de chaque set entre parenthèses, un format bien plus simple à parser
  que la chaîne Mortal Kombat (pas de type de finish à décoder, juste des points).
- **Groupes de marchés observés sur un match en direct** (non encore recoupés avec le dictionnaire
  officiel CDN, à faire) : `groupId 17` (Total de points du set, ex. plus/moins de 18.5),
  `groupId 2` (handicap, ex. ±2.5), `groupId 14` (deux issues seulement, probablement vainqueur du
  set), `groupId 60` (plusieurs paramètres 5/7/9, signification non établie).

**Volume confirmé** : 134 matchs/24h par salle (2 salles, 268/24h cumulé) — modeste comparé à
Mortal Kombat (570/24h sur 2 ligues) mais avec une granularité par set potentiellement aussi riche
que Mortal Kombat par manche, sur un sport dont le forecasting est un domaine mature (tennis de
table : probabilité de gain par point, modèles déjà publiés dans la littérature sportive).

**État à la fin de cette reconnaissance initiale** : suffisant pour confirmer que c'est un
candidat sérieux (infrastructure connue, score propre, cadence correcte), mais **pas encore assez
pour coder un collecteur** — reste à faire, dans le même esprit que la reconnaissance Mortal
Kombat originale (section 2 et suivantes) : décodage complet des groupes de marchés via le
dictionnaire CDN, capture d'un match complet pour confirmer l'absence de piège (ex. l'égalité
2:2 trouvée en plein développement pour Mortal Kombat 3), et une vérification que les probabilités
simulées corrèlent bien avec un classement réel avant d'investir dans des variables « identité du
joueur ».
