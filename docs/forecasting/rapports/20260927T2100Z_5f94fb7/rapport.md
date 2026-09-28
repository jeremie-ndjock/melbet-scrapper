# Rapport d'évaluation des modèles prédictifs

- Date de référence (`as_of`) : 2026-09-27T21:00:00+00:00
- Version du code : `5f94fb7`
- Délai de disponibilité d'un match : 18 min

Rappel : le seul critère de succès est d'apporter une information que la cote du bookmaker n'a pas déjà (plan d'entraînement, sections 7.3 et 8). « Combinaison » = modèle combiné au marché ; « marché recalibré » = le marché seul, recalibré sur la même période : c'est la référence du verdict.

## Vainqueur de manche — Mortal Kombat X

**Verdict : Signal prometteur, non démontré**

- Manches évaluées (test, avec cotes) : 7112 ; entraînement : 173574
- Modèle retenu (sur la validation) : LightGBM
- Poids du modèle dans la combinaison : 0.021 (IC 95 % [-0.319 ; 0.368]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 0.6892 | 0.4960 | 0.0040 | 0.0040 | 5 % |
| Marché brut (marge retirée) | 0.6645 | 0.4727 | 0.0182 | 0.0128 | 65 % |
| Marché recalibré | 0.6652 | 0.4733 | 0.0231 | 0.0215 | 70 % |
| Modèle seul | 0.6699 | 0.4777 | 0.0215 | 0.0147 | 60 % |
| Combinaison modèle + marché | 0.6652 | 0.4733 | 0.0233 | 0.0213 | 70 % |

- Écart de log-loss combinaison − marché recalibré : -0.00002 (IC ajusté pour K=4 essai(s) : [-0.00008 ; +0.00004])
- Écart combinaison − marché brut : +0.00065 [-0.00010 ; +0.00145]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.02 choisi sur la validation) : 0 paris, résultat +0 F, rendement — [— ; —]
Échantillon insuffisant pour conclure.

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.103, 0.103 — une valeur proche de 0 signifie une cote quasiment figée.

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

**Verdict : Aucun avantage détecté**

- Manches évaluées (test, avec cotes) : 5217 ; entraînement : 5359
- Modèle retenu (sur la validation) : régression logistique (variables fixées à l'avance)
- Poids du modèle dans la combinaison : — (IC 95 % [— ; —]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Marché brut (marge retirée) | 0.6924 | 0.4993 | 0.0223 | 0.0048 | 15 % |
| Marché recalibré | 0.6926 | 0.4994 | 0.0245 | 0.0081 | 10 % |
| Modèle (inclut la cote du marché) | 0.6934 | 0.5002 | 0.0253 | 0.0120 | 25 % |

- Écart de log-loss combinaison − marché recalibré : +0.00076 (IC ajusté pour K=4 essai(s) : [-0.00083 ; +0.00226])
- Écart combinaison − marché brut : +0.00093 [-0.00069 ; +0.00244]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.00 choisi sur la validation) : 2988 paris, résultat -67 444 F, rendement -2.3 % [-5.8 % ; +1.3 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 4095 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| moins | 1208 | -24 562 F | -2.0 % | 1.96 |
| plus | 1780 | -42 882 F | -2.4 % | 1.96 |

- Ici le modèle inclut directement la cote du marché parmi ses variables : il est comparé au marché seul recalibré (même protocole), le poids de combinaison est donc sans objet.
- Cotes écartées car postérieures à la manche : 0

## Vainqueur de manche — Mortal Kombat 3

**Verdict : Aucun avantage détecté**

- Manches évaluées (test, avec cotes) : 6989 ; entraînement : 171390
- Modèle retenu (sur la validation) : régression logistique
- Poids du modèle dans la combinaison : 0.163 (IC 95 % [-0.314 ; 0.613]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 0.6886 | 0.4955 | 0.0143 | 0.0143 | 5 % |
| Marché brut (marge retirée) | 0.6606 | 0.4685 | 0.0196 | 0.0164 | 80 % |
| Marché recalibré | 0.6606 | 0.4685 | 0.0197 | 0.0172 | 75 % |
| Modèle seul | 0.6634 | 0.4710 | 0.0195 | 0.0174 | 65 % |
| Combinaison modèle + marché | 0.6607 | 0.4686 | 0.0242 | 0.0163 | 75 % |

- Écart de log-loss combinaison − marché recalibré : +0.00010 (IC ajusté pour K=4 essai(s) : [-0.00018 ; +0.00040])
- Écart combinaison − marché brut : +0.00013 [-0.00017 ; +0.00042]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.02 choisi sur la validation) : 8 paris, résultat +760 F, rendement +9.5 % [-100.0 % ; +25.1 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 17548 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| joueur 2 | 1 | -1 000 F | -100.0 % | 3.46 |
| joueur 1 | 7 | +1 760 F | +25.1 % | 4.39 |

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.112, 0.112 — une valeur proche de 0 signifie une cote quasiment figée.

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

- Manches évaluées (test, avec cotes) : 6985 ; entraînement : 171390
- Modèle retenu (sur la validation) : régression logistique
- Poids du modèle dans la combinaison : 0.828 (IC 95 % [0.643 ; 1.046]) — un poids nul signifie que le modèle n'apporte rien au marché

| Probabilités | Log-loss | Brier | ECE (effectif égal) | ECE (largeur égale) | Intervalles non vides |
|---|---|---|---|---|---|
| Fréquences de base (historique) | 1.1288 | 0.6123 | 0.0012 | 0.0012 | 5 % |
| Marché brut (méthode power) | 1.1171 | 0.6020 | 0.0142 | 0.0114 | 20 % |
| Marché brut (marge retirée) | 1.1245 | 0.6016 | 0.0146 | 0.0116 | 19 % |
| Marché recalibré | 1.1173 | 0.6016 | 0.0135 | 0.0101 | 19 % |
| Modèle seul | 1.1057 | 0.6013 | 0.0114 | 0.0056 | 19 % |
| Combinaison modèle + marché | 1.1049 | 0.6010 | 0.0105 | 0.0058 | 19 % |

- Écart de log-loss combinaison − marché recalibré : -0.01243 (IC ajusté pour K=4 essai(s) : [-0.01628 ; -0.00845])
- Écart combinaison − marché brut : -0.01963 [-0.02351 ; -0.01526]
- Contrôle des étiquettes face au marché : réussi

**Backtest** (value bets, mise fixe 1 000 F, seuil d'avantage τ = 0.00 choisi sur la validation) : 2581 paris, résultat -560 113 F, rendement -21.7 % [-37.4 % ; -5.5 %]
Échantillon insuffisant pour conclure (il faudrait ≈ 84033 paris).

| Issue pariée | Paris | Résultat | Rendement | Cote moyenne |
|---|---|---|---|---|
| R | 11 | -3 504 F | -31.9 % | 2.41 |
| F | 203 | -25 809 F | -12.7 % | 4.44 |
| B | 721 | -6 800 F | -0.9 % | 12.44 |
| Ba | 1037 | -380 000 F | -36.6 % | 38.15 |
| Fr | 495 | -145 000 F | -29.3 % | 42.52 |
| An | 98 | +17 000 F | +17.3 % | 47.40 |
| Hk | 16 | -16 000 F | -100.0 % | 18.56 |

Variabilité du marché (écart-type de sa probabilité, par issue) : 0.071, 0.080, 0.014, 0.010, 0.005, 0.001, 0.008 — une valeur proche de 0 signifie une cote quasiment figée.

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

## Modèles de production (réentraînement quotidien)

Chaque modèle est réentraîné sur les données récentes, puis comparé au modèle en place sur le dernier jour, qu'aucun des deux n'a vu. Il n'est promu que s'il fait au moins aussi bien.

| Cible | Décision | Modèle | Entraînement | Jour d'examen | Log-loss nouveau | Log-loss en place | Raison |
|---|---|---|---|---|---|---|---|
| vainqueur_1252965 | promu | LightGBM | 192792 | 2054 | 0.6653 | — | premier modèle de production |
| duree_1252965 | promu | régression logistique (inclut la cote du marché) | 8623 | 2054 | 0.6939 | — | premier modèle de production |
| vainqueur_2282406 | promu | régression logistique | 189593 | 2003 | 0.6514 | — | premier modèle de production |
| finish_2282406 | promu | régression logistique | 189593 | 2003 | 1.1168 | — | premier modèle de production |

## Qualité des données

```
{
  "1252965": {
    "couverture_duree_par_manche": {
      "1": 1.0,
      "2": 1.0,
      "3": 1.0,
      "4": 1.0,
      "5": 0.9993,
      "6": 1.0,
      "7": 0.9952,
      "8": 0.9971,
      "9": 0.9972
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
    "lignes_de_cotes": 208215,
    "manches_brutes": 199196,
    "manches_retenues": 199156,
    "manches_sans_finish": 0,
    "matchs_bruts": 27321,
    "matchs_compares_direct": 1482,
    "matchs_retenus": 27309
  },
  "2282406": {
    "couverture_duree_par_manche": {
      "1": 1.0,
      "2": 1.0,
      "3": 1.0,
      "4": 0.9993,
      "5": 0.9993,
      "6": 0.9985,
      "7": 0.9951,
      "8": 0.9954,
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
    "lignes_de_cotes": 216230,
    "manches_brutes": 195929,
    "manches_retenues": 195825,
    "manches_sans_finish": 0,
    "matchs_bruts": 26895,
    "matchs_compares_direct": 1475,
    "matchs_retenus": 26870
  },
  "delai_disponibilite": {
    "mediane_duree_match_s": 725.140195,
    "p99_duree_match_s": 1071.700849
  }
}
```
