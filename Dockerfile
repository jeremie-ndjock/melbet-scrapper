# syntax=docker/dockerfile:1
# Étape 3 : cibles `base` et `dev` (tests). La cible `runtime` (production) sera ajoutée à l'étape 10.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM base AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
ENV PYTHONPATH=/app/src
USER app
CMD ["pytest"]

# Étapes 6 et 8 : image minimale d'exécution du collecteur. `migrations/` est indispensable : le
# programme les applique lui-même au démarrage (voir main.py) — un oubli ici passe inaperçu tant
# qu'on ne teste qu'avec le conteneur `tests`, qui monte tout le dépôt en volume et masque le
# problème (trouvé lors d'un essai réel de déploiement, étape 8). Durcissement complet
# (utilisateur non root déjà en place ; journaux, image plus petite) prévu à l'étape 10.
FROM base AS runtime
COPY src ./src
COPY config ./config
COPY migrations ./migrations
# Portraits des combattants (annonce pré-match, voir fighter_images.py) : dossier facultatif, une
# image manquante ne bloque jamais rien, mais quand il existe il doit être embarqué dans l'image.
COPY assets ./assets
ENV PYTHONPATH=/app/src
USER app
CMD ["python", "-m", "collector.main"]
