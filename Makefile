# Raccourcis. Sous Windows sans `make`, lancer directement les commandes `docker compose` indiquées.
.PHONY: db-up db-down migrate test psql

db-up:            ## démarre la base
	docker compose up -d db

db-down:          ## arrête la base (les données restent dans le volume)
	docker compose down

migrate:          ## applique les migrations SQL
	docker compose --profile test run --rm tests python -m collector.storage.migrate

test:             ## lance les tests d'intégration
	docker compose --profile test run --rm tests

psql:             ## console SQL
	docker compose exec db sh -c 'psql -U "$$POSTGRES_USER" "$$POSTGRES_DB"'
