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
- **Variables** : `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_FROM`, `EMAIL_TO`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_USERNAME`. Le `chat_id` a été renseigné automatiquement après le `/start` de l'utilisateur.
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

**Prochaine étape : 8, observabilité et alertes** (métriques Prometheus, tableaux de bord Grafana,
branchement réel des alertes Telegram et e-mail déjà configurées, script de surveillance externe).

---

Statut : reconnaissance terminée, architecture validée, **étapes 3 à 7 terminées et testées (140 tests, plus des vérifications réelles à chaque étape)**. Le collecteur s'identifie honnêtement, ne contourne jamais un blocage, bascule automatiquement sur une source de secours en cas de changement de structure, et s'arrête proprement sur un vrai blocage (section 20). Prochaine étape : 8 (observabilité, alertes). Décisions : option A ; rétention indéfinie ; deux ligues (Mortal Kombat X et Mortal Kombat 3) ; alerting e-mail et Telegram (configurés, pas encore branchés au code) ; sauvegardes quotidiennes sur le VPS et récupération par l'utilisateur ; pas d'accès au VPS pour l'instant (développement local dans Docker).
