# Runbook de déploiement — VPS AWS EC2

Ce document décrit la mise en service du collecteur sur un VPS réel (étape 11 du plan, voir
`docs/architecture.md` §14 et `Memoire.md` section 13). Choix retenus avec l'utilisateur le
2026-09-22 : instance **m7i-flex.large** (2 vCPU, 8 Go RAM — conforme à l'architecture validée ;
`t3.large` visée initialement n'était pas proposée par l'assistant de lancement simplifié d'AWS
sur ce compte, `m7i-flex.large` est l'équivalent exact en vCPU/RAM parmi les options offertes),
région **eu-west-3 (Paris)**, budget initial **100 $ de crédit AWS**.

**Aucune valeur secrète (mots de passe, clés) n'apparaît dans ce fichier.** Il décrit une
procédure, pas des identifiants.

## 1. Pourquoi ces choix

- **m7i-flex.large** (2 vCPU, 8 Go) plutôt qu'une instance à 1 ou 2 Go de RAM : l'architecture
  validée demande 2 vCPU / 8 Go pour tenir un cycle de 5 s sur deux ligues avec de la marge.
  Coût estimé (à vérifier sur la calculatrice AWS au moment de la création, les tarifs évoluent) :
  de l'ordre de 55 à 70 $/mois en fonctionnement continu selon la région — le crédit de 100 $
  couvre environ 6 semaines de fonctionnement 24/7. **Prévoir un suivi de la consommation dès le
  départ** (§7).
- **eu-west-3 (Paris)** plutôt que l'idée initiale « Pays-Bas » (AWS n'a pas de région
  néerlandaise) : bonne latence Europe/Afrique de l'Ouest, région mature. Aucune protection
  anti-bot n'ayant été observée sur le site cible (voir Memoire.md, section 4), la géographie
  n'affecte que la latence, pas la faisabilité — un déplacement de région plus tard reste possible
  sans remise en cause de l'architecture.
- **50 Go, gp3 (SSD)** : conforme à l'architecture validée.
- **Ubuntu Server 22.04 LTS (ou plus récent au moment de la création)** : support à long terme,
  Docker officiellement supporté.

## 2. Création de l'instance (console AWS)

1. Se connecter à la console AWS, changer la région (en haut à droite) pour **Europe (Paris)
   eu-west-3**.
2. Aller dans **EC2 → Instances → Launch instance**.
3. **Name** : `oddscollector-vps`.
4. **Application and OS Images** : **Ubuntu Server 24.04 LTS** (64-bit x86 — pas la variante
   « Pro », qui ajoute des fonctionnalités payantes inutiles ici ; pas la LTS la plus récente
   proposée au moment du choix (26.04) : une LTS avec un peu plus de recul est préférable pour un
   service qui doit tourner 24/7 sans surprise).
5. **Instance type** : `m7i-flex.large` (2 vCPU, 8 Go RAM).
6. **Key pair (login)** : créer une nouvelle paire (`oddscollector-key`), format `.pem`,
   télécharger le fichier et le conserver hors du dépôt git — **il ne doit jamais être commité**
   (déjà couvert par la règle générale « aucun secret dans le dépôt »).
7. **Network settings** :
   - Créer un nouveau groupe de sécurité, nom `oddscollector-sg`.
   - Autoriser uniquement le SSH (port 22), **source « My IP »**, jamais `0.0.0.0/0` — aucun autre
     port entrant n'est nécessaire : Grafana (3000), Prometheus et PostgreSQL restent internes au
     réseau Docker de la machine, jamais publiés (décision D4, voir `docs/architecture.md`), donc
     rien d'autre à ouvrir dans ce groupe de sécurité.
8. **Configure storage** : 50 GiB, type **gp3**.
9. **Launch instance**.

## 3. Adresse IP stable (Elastic IP)

Par défaut, l'adresse IP publique d'une instance change si elle est arrêtée puis redémarrée.
Pour une adresse stable (utile pour le tunnel SSH Grafana, et pour retrouver le serveur) :

1. **EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address**.
2. **Actions → Associate Elastic IP address**, sélectionner l'instance `oddscollector-vps`.

Une IP Elastic est gratuite tant qu'elle est associée à une instance **en cours d'exécution** ;
elle est facturée si elle reste allouée sans instance active — à libérer si l'instance est arrêtée
durablement.

## 4. Connexion et préparation du serveur

Depuis la machine locale (remplacer `<IP>` par l'IP Elastic, et le chemin de la clé) :

```bash
chmod 400 oddscollector-key.pem
ssh -i oddscollector-key.pem ubuntu@<IP>
```

Sur le serveur :

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get -y install ca-certificates curl gnupg git

# Docker Engine + Compose plugin (dépôt officiel Docker, pas le paquet distro, souvent obsolète)
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Éviter de préfixer chaque commande docker de `sudo`
sudo usermod -aG docker ubuntu
# se déconnecter / reconnecter (ou `newgrp docker`) pour que ça prenne effet

# Pare-feu applicatif en plus du groupe de sécurité AWS (défense en profondeur) :
# seul SSH est nécessaire, tout le reste (Grafana, Prometheus, DB) reste sur le réseau Docker.
sudo apt-get -y install ufw
sudo ufw allow OpenSSH
sudo ufw --force enable
```

Vérifier :

```bash
docker --version
docker compose version
```

## 5. Déploiement du projet

```bash
cd /opt
sudo mkdir oddscollector && sudo chown ubuntu:ubuntu oddscollector
git clone https://github.com/jeremie-ndjock/melbet-scrapper.git oddscollector
cd oddscollector

cp .env.example .env
nano .env   # renseigner EMAIL_*, TELEGRAM_*, POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD
            # (générer des mots de passe forts, ex. `openssl rand -hex 24`)

docker compose up -d db scraper prometheus grafana
```

**Alternative** : copier un `.env` déjà rempli depuis la machine locale plutôt que de tout
ressaisir (`scp -i oddscollector-key.pem .env ubuntu@<IP>:/opt/oddscollector/.env`). **Piège réel
rencontré au premier déploiement** : un `.env` édité sous Windows a des fins de ligne CRLF ; une
fois copié tel quel, tout script qui fait `source .env` sous Linux (c'est le cas de
`scripts/backup_db.sh` et `scripts/restore_db.sh`) échoue ou pire, charge des valeurs avec un `\r`
invisible en fin de chaîne (ex. un rôle PostgreSQL "collector" avec un `\r` caché, rejeté comme
inexistant). Ces deux scripts ont depuis été corrigés pour normaliser les fins de ligne
automatiquement, mais par précaution, après un `scp` depuis Windows :

```bash
sed -i 's/\r$//' .env
```

## 6. Vérifications post-déploiement

```bash
docker compose ps                                    # tous les services "healthy"
docker compose logs scraper --tail=30                # migrations appliquées, premiers cycles OK
```

Depuis la machine locale, tunnel SSH pour Grafana (jamais exposé publiquement, décision D4) :

```bash
ssh -i oddscollector-key.pem -L 3000:localhost:3000 ubuntu@<IP>
# puis ouvrir http://localhost:3000 dans un navigateur local
```

**Test spécifique à cette étape, impossible à faire avant d'avoir un vrai VPS** : confirmer que le
site répond normalement depuis la véritable adresse IP du VPS (aucune raison de penser qu'il en
irait autrement — aucune protection anti-bot détectée lors de la reconnaissance, voir Memoire.md
section 4 — mais seule une vérification réelle en a la certitude). Les journaux du conteneur
`scraper` (ci-dessus) suffisent à le confirmer : des cycles réussis, sans blocage 403/429.

## 7. Tâches planifiées (cron)

```bash
crontab -e
```

Ajouter :

```cron
# Sauvegarde quotidienne (voir scripts/backup_db.sh)
0 3 * * * cd /opt/oddscollector && ./scripts/backup_db.sh >> /var/log/oddscollector-backup.log 2>&1

# Surveillance externe, toutes les 5 minutes (voir scripts/watchdog.py)
*/5 * * * * cd /opt/oddscollector && docker compose --profile test run --rm tests python scripts/watchdog.py >> /var/log/oddscollector-watchdog.log 2>&1
```

## 8. Suivi des coûts (crédit de 100 $)

Dès la création, dans la console AWS :

1. **Billing and Cost Management → Budgets → Create budget**.
2. Budget de type « Cost budget », montant **80 $** (marge avant d'épuiser le crédit), alerte par
   e-mail à 50 %, 80 % et 100 % du seuil.
3. Vérifier régulièrement **Billing → Cost Explorer** pour suivre le rythme de consommation réel
   de l'instance `m7i-flex.large` et ajuster si besoin (arrêt temporaire, changement de taille).

## 9. Récupération / sinistre

- Sauvegardes locales sur le VPS : voir `scripts/backup_db.sh` (rotation automatique).
- Récupération régulière (hebdomadaire recommandé) vers une machine hors VPS :
  `scp -i oddscollector-key.pem ubuntu@<IP>:/opt/oddscollector/backups/odds_*.sql.gz .`
- Restauration sur un nouveau serveur : voir `scripts/restore_db.sh` (utilise les fonctions
  officielles TimescaleDB `timescaledb_pre_restore()`/`timescaledb_post_restore()`, indispensables
  — voir Memoire.md, étape 10, pour le défaut réel que cela corrige).

## Statut

En attente d'exécution par l'utilisateur (création de l'instance dans la console AWS). Ce document
sera mis à jour avec les résultats réels une fois le VPS créé et le déploiement effectué (voir
Memoire.md, section 21, étape 11).
