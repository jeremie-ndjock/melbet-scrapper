# Rapport d'évaluation des modèles prédictifs

- Date de référence (`as_of`) : 2026-09-25T15:00:00+00:00
- Version du code : `486b4ff`
- Délai de disponibilité d'un match : 18 min

Rappel : le seul critère de succès est d'apporter une information que la cote du bookmaker n'a pas déjà (plan d'entraînement, sections 7.3 et 8). « Combinaison » = modèle combiné au marché ; « marché recalibré » = le marché seul, recalibré sur la même période : c'est la référence du verdict.

## Vainqueur de manche — Mortal Kombat X

**Verdict : Aucun avantage détecté**

- Manches évaluées (test, avec cotes) : 3978 ; entraînement : 173574
- Modèle retenu (sur la validation) : LightGBM
- Poids du modèle dans la combinaison : -0.034 (IC 95 % [-0.522 ; 0.395]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 0.6889 | 0.4957 | 0.0025 | 0.0025 | 5 % |
| Marché brut (marge retirée) | 0.6636 | 0.4717 | 0.0212 | 0.0127 | 60 % |
| Marché recalibré | 0.6638 | 0.4720 | 0.0236 | 0.0192 | 60 % |
| Modèle seul | 0.6695 | 0.4773 | 0.0291 | 0.0135 | 60 % |
| Combinaison modèle + marché | 0.6639 | 0.4720 | 0.0235 | 0.0185 | 60 % |

- Écart de log-loss combinaison − marché recalibré : +0.00004 (IC ajusté pour K=3 essai(s) : [-0.00008 ; +0.00017])
- Écart combinaison − marché brut : +0.00023 [-0.00063 ; +0.00113]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.02 choisi sur la validation) : 0 paris, résultat +0 F, rendement — [— ; —]
Échantillon insuffisant pour conclure.

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.101, 0.101 — une valeur proche de 0 signifie une cote quasiment figée.

**Validation glissante sur l'historique (sans cotes)** — log-loss par semaine :

| Semaine | n | Fréquences de base | Modèle | + heure | + séries récentes |
|---|---|---|---|---|---|
| 2026-08-04 | 14648 | 0.6887 | 0.6757 | 0.6756 | 0.6758 |
| 2026-08-11 | 14522 | 0.6885 | 0.6742 | 0.6742 | 0.6742 |
| 2026-08-18 | 14677 | 0.6876 | 0.6729 | 0.6730 | 0.6731 |
| 2026-08-25 | 14617 | 0.6889 | 0.6753 | 0.6753 | 0.6752 |
| 2026-09-01 | 14531 | 0.6882 | 0.6721 | 0.6719 | 0.6722 |
| 2026-09-08 | 14639 | 0.6875 | 0.6740 | 0.6740 | 0.6740 |

- Cotes écartées car postérieures à la manche : 0

## Durée de manche — Mortal Kombat X (P(durée > ligne principale))

**PRÉLIMINAIRE (≈ 3 jours de données)** — aucun chiffre ci-dessous ne permet encore de conclure.

**Verdict : Signal prometteur, non démontré**

- Manches évaluées (test, avec cotes) : 2835 ; entraînement : 3003
- Modèle retenu (sur la validation) : régression logistique (variables fixées à l'avance)
- Poids du modèle dans la combinaison : — (IC 95 % [— ; —]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Marché brut (marge retirée) | 0.6936 | 0.5005 | 0.0413 | 0.0219 | 15 % |
| Marché recalibré | 0.6935 | 0.5003 | 0.0392 | 0.0209 | 15 % |
| Modèle (inclut la cote du marché) | 0.6931 | 0.5000 | 0.0345 | 0.0137 | 30 % |

- Écart de log-loss combinaison − marché recalibré : -0.00034 (IC ajusté pour K=3 essai(s) : [-0.00235 ; +0.00186])
- Écart combinaison − marché brut : -0.00048 [-0.00257 ; +0.00178]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.05 choisi sur la validation) : 104 paris, résultat -3 917 F, rendement -3.8 % [-20.8 % ; +11.9 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 4159 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| moins | 88 | -5 805 F | -6.6 % | 1.95 |
| plus | 16 | +1 888 F | +11.8 % | 1.97 |

- Ici le modèle inclut directement la cote du marché parmi ses variables : il est comparé au marché seul recalibré (même protocole), le poids de combinaison est donc sans objet.
- Cotes écartées car postérieures à la manche : 0

## Vainqueur de manche — Mortal Kombat 3

**Verdict : Aucun avantage détecté**

- Manches évaluées (test, avec cotes) : 3916 ; entraînement : 171390
- Modèle retenu (sur la validation) : régression logistique
- Poids du modèle dans la combinaison : 0.009 (IC 95 % [-0.684 ; 0.699]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 0.6908 | 0.4977 | 0.0014 | 0.0014 | 5 % |
| Marché brut (marge retirée) | 0.6607 | 0.4685 | 0.0257 | 0.0201 | 75 % |
| Marché recalibré | 0.6625 | 0.4702 | 0.0347 | 0.0266 | 75 % |
| Modèle seul | 0.6638 | 0.4714 | 0.0261 | 0.0240 | 60 % |
| Combinaison modèle + marché | 0.6625 | 0.4702 | 0.0337 | 0.0274 | 70 % |

- Écart de log-loss combinaison − marché recalibré : +0.00000 (IC ajusté pour K=3 essai(s) : [-0.00002 ; +0.00002])
- Écart combinaison − marché brut : +0.00178 [+0.00014 ; +0.00337]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.02 choisi sur la validation) : 616 paris, résultat -68 904 F, rendement -11.2 % [-20.3 % ; -1.7 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 6618 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| joueur 2 | 616 | -68 904 F | -11.2 % | 2.63 |

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.110, 0.110 — une valeur proche de 0 signifie une cote quasiment figée.

**Validation glissante sur l'historique (sans cotes)** — log-loss par semaine :

| Semaine | n | Fréquences de base | Modèle | + heure | + séries récentes |
|---|---|---|---|---|---|
| 2026-08-04 | 14424 | 0.6908 | 0.6654 | 0.6653 | 0.6652 |
| 2026-08-11 | 14517 | 0.6904 | 0.6701 | 0.6702 | 0.6702 |
| 2026-08-18 | 13789 | 0.6920 | 0.6645 | 0.6639 | 0.6640 |
| 2026-08-25 | 14526 | 0.6903 | 0.6634 | 0.6636 | 0.6636 |
| 2026-09-01 | 14522 | 0.6906 | 0.6685 | 0.6685 | 0.6685 |
| 2026-09-08 | 14662 | 0.6900 | 0.6713 | 0.6712 | 0.6712 |

- Cotes écartées car postérieures à la manche : 0

## Type de finish — Mortal Kombat 3 (7 classes)

**Verdict : Signal prometteur, non démontré**

- Manches évaluées (test, avec cotes) : 3916 ; entraînement : 171390
- Modèle retenu (sur la validation) : régression logistique
- Poids du modèle dans la combinaison : 0.766 (IC 95 % [0.562 ; 1.076]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 1.1383 | 0.6180 | 0.0032 | 0.0032 | 5 % |
| Marché brut (méthode power) | 1.1339 | 0.6093 | 0.0172 | 0.0128 | 20 % |
| Marché brut (marge retirée) | 1.1390 | 0.6082 | 0.0173 | 0.0118 | 19 % |
| Marché recalibré | 1.1334 | 0.6086 | 0.0161 | 0.0113 | 19 % |
| Modèle seul | 1.1181 | 0.6083 | 0.0115 | 0.0054 | 19 % |
| Combinaison modèle + marché | 1.1176 | 0.6078 | 0.0128 | 0.0062 | 17 % |

- Écart de log-loss combinaison − marché recalibré : -0.01578 (IC ajusté pour K=3 essai(s) : [-0.02033 ; -0.01117])
- Écart combinaison − marché brut : -0.02136 [-0.02558 ; -0.01701]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.00 choisi sur la validation) : 1267 paris, résultat -119 580 F, rendement -9.4 % [-35.1 % ; +17.4 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 96385 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| R | 1 | -1 000 F | -100.0 % | 2.30 |
| F | 94 | -14 580 F | -15.5 % | 4.94 |
| B | 364 | +114 000 F | +31.3 % | 12.63 |
| Ba | 448 | -243 000 F | -54.2 % | 38.54 |
| Fr | 292 | +48 000 F | +16.4 % | 42.79 |
| An | 58 | -13 000 F | -22.4 % | 46.47 |
| Hk | 10 | -10 000 F | -100.0 % | 20.70 |

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.071, 0.080, 0.014, 0.010, 0.005, 0.001, 0.007 — une valeur proche de 0 signifie une cote quasiment figée.

**Validation glissante sur l'historique (sans cotes)** — log-loss par semaine :

| Semaine | n | Fréquences de base | Modèle | + heure | + séries récentes |
|---|---|---|---|---|---|
| 2026-08-04 | 14424 | 1.1214 | 1.1012 | 1.1011 | 1.1009 |
| 2026-08-11 | 14517 | 1.1278 | 1.1090 | 1.1094 | 1.1093 |
| 2026-08-18 | 13789 | 1.1138 | 1.0922 | 1.0921 | 1.0924 |
| 2026-08-25 | 14526 | 1.1158 | 1.0961 | 1.0962 | 1.0963 |
| 2026-09-01 | 14522 | 1.1218 | 1.1045 | 1.1048 | 1.1047 |
| 2026-09-08 | 14662 | 1.1165 | 1.0953 | 1.0952 | 1.0960 |

- Cotes écartées car postérieures à la manche : 0
- Aucune pondération des classes rares (elle fausserait les probabilités) : un rappel proche de 0 sur Hara-Kiri ou Animality est attendu d'un modèle bien calibré.
- Environ 0,15 % de Hara-Kiri : aucun chiffre par classe n'est significatif pour les classes rares.

## Qualité des données

```
{
  "1252965": {
    "couverture_duree_par_manche": {
      "1": 1.0,
      "2": 1.0,
      "3": 1.0,
      "4": 1.0,
      "5": 0.9988,
      "6": 1.0,
      "7": 0.9914,
      "8": 0.9949,
      "9": 0.9951
    },
    "debut_cotes": "2026-09-22T17:25:35+00:00",
    "exclus_egalite": 12,
    "exclus_orientation_inversee": 0,
    "exclus_sans_manches": 0,
    "exclus_score_incoherent": 0,
    "exclus_trous_manches": 0,
    "exclus_vainqueur_manche_inconnu": 0,
    "finish_desaccord_direct_officiel": 0,
    "identifiants_a_plusieurs_noms": 0,
    "lignes_de_cotes": 117231,
    "manches_brutes": 194459,
    "manches_retenues": 194419,
    "manches_sans_finish": 0,
    "matchs_bruts": 26673,
    "matchs_compares_direct": 834,
    "matchs_retenus": 26661
  },
  "2282406": {
    "couverture_duree_par_manche": {
      "1": 1.0,
      "2": 1.0,
      "3": 1.0,
      "4": 0.9988,
      "5": 0.9988,
      "6": 0.9973,
      "7": 0.9913,
      "8": 0.9947,
      "9": 1.0
    },
    "debut_cotes": "2026-09-22T17:25:37+00:00",
    "exclus_egalite": 25,
    "exclus_orientation_inversee": 0,
    "exclus_sans_manches": 0,
    "exclus_score_incoherent": 0,
    "exclus_trous_manches": 0,
    "exclus_vainqueur_manche_inconnu": 0,
    "finish_desaccord_direct_officiel": 0,
    "identifiants_a_plusieurs_noms": 0,
    "lignes_de_cotes": 122402,
    "manches_brutes": 191289,
    "manches_retenues": 191185,
    "manches_sans_finish": 0,
    "matchs_bruts": 26251,
    "matchs_compares_direct": 831,
    "matchs_retenus": 26226
  },
  "delai_disponibilite": {
    "mediane_duree_match_s": 725.354444,
    "p99_duree_match_s": 1068.15234
  }
}
```
