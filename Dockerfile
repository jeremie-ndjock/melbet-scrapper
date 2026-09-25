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

# Dépendances d'entraînement des modèles (pandas, scikit-learn, LightGBM). LightGBM a besoin de
# la bibliothèque OpenMP (libgomp1), absente de l'image slim. Jamais dans l'image `runtime` : le
# collecteur de production n'en a pas besoin.
FROM base AS ml-deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-ml.txt .
RUN pip install -r requirements-ml.txt

# `dev` inclut aussi les dépendances ML, pour qu'une seule suite pytest couvre `forecasting`.
FROM ml-deps AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
ENV PYTHONPATH=/app/src
USER app
CMD ["pytest"]

# Entraînement et évaluation des modèles (src/forecasting), sur le VPS, service `ml` de
# docker-compose.yml. Un seul fil de calcul : le collecteur garde la priorité sur les 2 vCPU.
FROM ml-deps AS ml
COPY src ./src
COPY config ./config
RUN mkdir -p /app/reports /app/artifacts && chown app:app /app/reports /app/artifacts
ARG GIT_COMMIT=inconnu
ENV PYTHONPATH=/app/src \
    GIT_COMMIT=$GIT_COMMIT \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
USER app
CMD ["python", "-m", "forecasting", "run"]

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
