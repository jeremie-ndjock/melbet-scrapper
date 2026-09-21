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
