"""Entraînement et évaluation des modèles prédictifs (phase 2 du plan d'entraînement,
docs/forecasting/plan_entrainement_mortal_kombat.docx ; Memoire.md, section 38).

Paquet indépendant du collecteur : il n'est jamais importé par ``collector``, pour que l'image de
production reste sans pandas ni LightGBM. Il ne fait que lire la base (session en lecture seule).
"""
