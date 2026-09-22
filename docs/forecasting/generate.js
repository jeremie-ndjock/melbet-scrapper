const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, AlignmentType, TableOfContents, PageBreak,
  LevelFormat, convertInchesToTwip, VerticalAlign,
} = require("docx");

const FONT = "Calibri";
const MONO = "Consolas";
const ACCENT = "8B1A1A"; // rouge sombre, clin d'oeil au thème Mortal Kombat
const ACCENT2 = "2F2A2E";

const H1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 400, after: 200 },
  children: [new TextRun({ text, bold: true, color: ACCENT, font: FONT })],
});
const H2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 300, after: 150 },
  children: [new TextRun({ text, bold: true, color: ACCENT2, font: FONT })],
});
const H3 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 200, after: 100 },
  children: [new TextRun({ text, bold: true, font: FONT })],
});
const P = (text, opts = {}) => new Paragraph({
  spacing: { after: 160 },
  children: [new TextRun({ text, font: FONT, italics: !!opts.italics, bold: !!opts.bold })],
});
const Bullet = (text, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 80 },
  children: [new TextRun({ text, font: FONT })],
});
const Code = (lines) => new Paragraph({
  spacing: { after: 200, before: 100 },
  shading: { type: ShadingType.CLEAR, fill: "F2EFEF" },
  indent: { left: 200, right: 200 },
  children: lines.flatMap((l, i) => i === 0 ? [new TextRun({ text: l, font: MONO, size: 18 })]
    : [new TextRun({ text: l, font: MONO, size: 18, break: 1 })]),
});
const Note = (text) => new Paragraph({
  spacing: { after: 200, before: 100 },
  shading: { type: ShadingType.CLEAR, fill: "FCEEEE" },
  indent: { left: 200 },
  children: [new TextRun({ text: "⚠ " + text, font: FONT, italics: true, size: 20, color: ACCENT })],
});

function cell(text, opts = {}) {
  return new TableCell({
    width: { size: opts.width || 2000, type: WidthType.DXA },
    shading: opts.header ? { type: ShadingType.CLEAR, fill: ACCENT } : undefined,
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 80, bottom: 80, left: 100, right: 100 },
    children: [new Paragraph({
      children: [new TextRun({
        text, font: FONT, size: 19,
        bold: !!opts.header, color: opts.header ? "FFFFFF" : "000000",
      })],
    })],
  });
}

function table(widths, rows) {
  return new Table({
    columnWidths: widths,
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    rows: rows.map((r, i) => new TableRow({
      children: r.map((c, j) => cell(c, { width: widths[j], header: i === 0 })),
    })),
  });
}

const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 400, hanging: 200 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 800, hanging: 200 } } } },
      ],
    }],
  },
  sections: [{
    properties: {
      page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, bottom: 1080, left: 1080, right: 1080 } },
    },
    children: [
      // ---------------------------------------------------------------- page de titre
      new Paragraph({ spacing: { before: 1200, after: 100 }, alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "PLAN D'ENTRAÎNEMENT", bold: true, size: 56, color: ACCENT, font: FONT })] }),
      new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Modèles prédictifs en temps réel — manches de Mortal Kombat", size: 32, font: FONT, color: ACCENT2 })] }),
      new Paragraph({ spacing: { after: 600 }, alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Collecteur de cotes MelBet Cameroun — Mortal Kombat X & Mortal Kombat 3", italics: true, size: 22, font: FONT })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
        children: [new TextRun({ text: "Préparé pour Jeremie Ndjock", size: 20, font: FONT })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
        children: [new TextRun({ text: "22 septembre 2026", size: 20, font: FONT })] }),
      new Paragraph({ children: [new PageBreak()] }),

      // ---------------------------------------------------------------- sommaire
      H1("Sommaire"),
      new TableOfContents("Sommaire", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),

      // ================================================================ 1. OBJECTIFS
      H1("1. Objectifs et périmètre"),
      P("Ce document détaille le plan d'entraînement de deux modèles prédictifs distincts, un par ligue, tous deux alimentés en temps réel par les données déjà collectées par le pipeline en production (voir Memoire.md et docs/architecture.md pour le contexte complet du système de collecte)."),

      H2("1.1 Modèle Mortal Kombat X — durée de la manche en cours"),
      P("Pour un match en cours de Mortal Kombat X, prédire, dès le début d'une manche, la probabilité que sa durée soit supérieure ou inférieure à un seuil donné. Sortie : une probabilité (pas seulement une classe), pour rester comparable au marché « Durée du Round » déjà proposé par le bookmaker (groupe 1074)."),

      H2("1.2 Modèle Mortal Kombat 3 — type de finish de la manche en cours"),
      P("Pour un match en cours de Mortal Kombat 3, prédire, dès le début d'une manche (ou dès que son issue devient imminente), le type de finish qui la conclura : Regular, Fatality, Brutality, Babality, Friendship, Animality ou Hara-Kiri. Sortie : une distribution de probabilité sur les 7 classes, pas seulement la classe la plus probable."),

      H2("1.3 Ce que ces modèles ne feront pas — limites assumées dès le départ"),
      Bullet("Ni l'un ni l'autre ne visera une exactitude parfaite : ce sont des matchs simulés (esport virtuel), pas des joueurs humains — la durée et l'issue d'une manche sortent d'un générateur du fournisseur du jeu, dont on ignore les paramètres exacts. Il existe donc un plancher d'aléa irréductible."),
      Bullet("Aucun seuil de performance n'est promis à l'avance : le vrai critère de succès est de faire mieux que la cote déjà publiée par le bookmaker (section 8), pas d'atteindre un chiffre absolu déconnecté du marché."),
      Bullet("Ce document ne couvre pas l'intégration temps réel dans le collecteur existant (Telegram, alerting) : elle est esquissée en section 11 comme une phase ultérieure, hors du périmètre d'entraînement lui-même."),

      // ================================================================ 2. DONNÉES SOURCES
      H1("2. Données sources (rappel du schéma)"),
      P("Toutes les tables ci-dessous existent déjà en production (voir migrations/001 à 008) et sont alimentées en continu, avec une rétention indéfinie."),
      table([2200, 6400], [
        ["Table", "Champs utiles pour ces deux modèles"],
        ["events", "league_id, p1_name, p2_name, start_ts, status — identité du match et des deux combattants"],
        ["round_results", "round_no, winner, seconds (durée — cible du modèle MKX), finish_di (type de finish — cible du modèle MK3), fw, wt"],
        ["game_state", "score1/score2, period, elapsed_s — évolution du score en direct"],
        ["odds_snapshots", "g=1074 (Durée du Round, Plus/Moins) : ligne et cote de référence pour le modèle MKX. g=1066 (Mode de Victoire : Fatality/Brutality/No Finish) et g=3533 (Fatality dans Round, Oui/Non) : référence partielle pour le modèle MK3."],
        ["results", "final_score1/2, winner, date_start — rattrapage historique, utile pour enrichir l'historique par combattant au-delà de ce que le direct a capté"],
      ]),
      Note("Le marché « Mode de Victoire » (groupe 1066) ne distingue que 3 issues (Fatality / Brutality / No Finish), plus grossier que les 7 classes visées par le modèle MK3 — il sert de repère de comparaison partiel, pas de vérité terrain complète."),

      // ================================================================ 3. CIBLES
      H1("3. Définition précise des cibles"),
      H2("3.1 Modèle Mortal Kombat X : cible binaire ancrée sur le marché"),
      P("Plutôt qu'un seuil fixe arbitraire (ex. « plus ou moins de 30 secondes », qui n'aurait aucun sens si le marché coté ce jour-là est « plus ou moins de 45 secondes »), la cible est définie relativement à la ligne réellement proposée par le marché « Durée du Round » (groupe 1074) au moment de la manche :"),
      Code(["y = 1  si durée_réelle_de_la_manche > ligne_du_marché_à_cet_instant", "y = 0  sinon"]),
      P("Cette définition rend le modèle directement comparable à la cote du bookmaker (section 8) et évite un seuil arbitraire qui deviendrait obsolète si le fournisseur change ses lignes."),

      H2("3.2 Modèle Mortal Kombat 3 : cible multiclasse, fortement déséquilibrée"),
      P("Cible : finish_di ∈ {Regular, Fatality, Brutality, Babality, Friendship, Animality, Hara-Kiri}. Distribution mesurée sur Mortal Kombat 3 (Memoire.md, section 15, 24 h de relevé) :"),
      table([2600, 2000, 2000, 2000], [
        ["Type de finish", "Fréquence MK3", "≈ manches/jour", "Manches pour 100 exemples"],
        ["Regular", "48,4 %", "≈ 1 017", "< 1 jour"],
        ["Fatality", "39,3 %", "≈ 825", "< 1 jour"],
        ["Brutality", "8,0 %", "≈ 168", "< 1 jour"],
        ["Babality", "2,0 %", "≈ 42", "≈ 2,4 jours"],
        ["Friendship", "1,6 %", "≈ 34", "≈ 3 jours"],
        ["Animality", "0,6 %", "≈ 13", "≈ 8 jours"],
        ["Hara-Kiri", "0,1 %", "≈ 2", "≈ 48 jours"],
      ]),
      Note("La classe Hara-Kiri est le vrai facteur limitant du calendrier de collecte pour ce modèle (section 5) : à ≈2 manches par jour, réunir ne serait-ce que 100 exemples prend près de 7 semaines. Options à évaluer le moment venu : regrouper les classes les plus rares avec Brutality dans une catégorie « finish spécial » pour un premier modèle, ou accepter une incertitude élevée sur ces classes en attendant plus de données."),

      // ================================================================ 4. FEATURES
      H1("4. Ingénierie des variables (features)"),
      H2("4.1 Variables communes aux deux modèles"),
      Bullet("round_no — le numéro de la manche en cours (une manche 1 ne se joue pas comme une manche décisive)"),
      Bullet("score1_avant / score2_avant — nombre de manches déjà gagnées par chaque combattant avant celle-ci (situation de match point ou non)"),
      Bullet("p1_name / p2_name — identité des deux combattants (encodage catégoriel : one-hot pour un premier modèle, cible-encodage ou embeddings si le volume le permet ensuite)"),
      Bullet("Historique glissant par combattant (calculé UNIQUEMENT sur les matchs antérieurs à l'instant de la prédiction, jamais sur le match en cours — voir 4.4) : durée moyenne de ses manches, répartition de ses types de finish, nombre de matchs déjà observés"),
      Bullet("Heure / jour de la semaine du match, à titre exploratoire (utile seulement si le fournisseur fait varier ses paramètres selon le moment — à vérifier empiriquement, pas supposé a priori)"),

      H2("4.2 Variables spécifiques au modèle Mortal Kombat X (durée)"),
      Bullet("Ligne et cote du marché « Durée du Round » (groupe 1074) au moment de la manche — à la fois la base de la cible (3.1) et une variable prédictive à part entière (la cote du bookmaker contient déjà de l'information)"),
      Bullet("Durée des manches précédentes du même match (effet de rythme/momentum intra-match)"),
      Bullet("fw (Flawless Victory) et mercy_p1/mercy_p2 de la manche précédente, si disponibles avant la manche courante"),

      H2("4.3 Variables spécifiques au modèle Mortal Kombat 3 (type de finish)"),
      Bullet("Cote du marché « Mode de Victoire » (groupe 1066) et « Fatality dans Round » (groupe 3533) au moment de la manche — repère de comparaison partiel (3 classes seulement) mais utile comme variable"),
      Bullet("Type de finish des manches précédentes du même match (un combattant qui vient d'enchaîner deux Fatality a peut-être une dynamique différente)"),
      Bullet("Répartition historique des types de finish par combattant ET par la paire de combattants (matchup) si le volume le permet (section 5)"),

      H2("4.4 Règle impérative anti-fuite (data leakage)"),
      Note("Toute variable agrégée par combattant (durée moyenne, répartition des finishes) doit être calculée en ne regardant QUE les matchs dont la date de début est antérieure à la manche à prédire. Calculer ces agrégats sur l'ensemble des données (passé ET futur) donnerait une performance artificiellement excellente en test, totalement inexploitable en production — c'est l'erreur la plus fréquente et la plus dangereuse dans ce genre de projet."),

      // ================================================================ 5. VOLUME NÉCESSAIRE
      H1("5. Volume de données nécessaire"),
      P("Il n'existe pas de durée de collecte qui rende un modèle « parfait ». Le tableau ci-dessous donne des ordres de grandeur réalistes selon la granularité visée, à partir des volumes mesurés en conditions réelles (≈ 2 084 manches/jour pour Mortal Kombat X, ≈ 2 101 pour Mortal Kombat 3, soit environ 320 matchs/jour par ligue en moyenne)."),
      table([3600, 2600, 2400], [
        ["Objectif du modèle", "Volume nécessaire", "Durée approximative"],
        ["Distribution générale (round_no, score, sans distinguer les combattants)", "Quelques milliers de manches", "3 à 7 jours"],
        ["Effet par combattant individuel (33 pour MKX, 32 pour MK3)", "≈ 100 manches par combattant", "2 à 3 semaines"],
        ["Effet par matchup (paire de combattants) — ≈ 528 paires possibles pour MKX, ≈ 496 pour MK3", "≈ 30 matchs par paire", "4 à 8 semaines, plus pour les paires rares"],
        ["Classe Hara-Kiri (MK3) à un niveau fiable (≈100 exemples)", "≈ 100 manches Hara-Kiri", "≈ 7 semaines (facteur limitant du projet MK3)"],
        ["Saisonnalité (jour de semaine, dérive éventuelle du fournisseur)", "Au moins 2 à 3 cycles complets", "3 à 4 semaines minimum"],
      ]),
      P("Recommandation retenue pour ce plan : ne pas attendre un seuil fixe avant de commencer. La collecte tourne déjà 24 h/24 avec une rétention indéfinie — un premier modèle baseline est entraîné dès J+3 à J+7 (section 10), puis réentraîné régulièrement à mesure que le volume grandit."),

      // ================================================================ 6. PIPELINE ETL
      H1("6. Pipeline de données (ETL)"),
      H2("6.1 Extraction : jeu de données au niveau manche"),
      P("Requête de départ pour Mortal Kombat X (remplacer league_id par 2282406 pour Mortal Kombat 3) :"),
      Code([
        "SELECT",
        "    rr.game_id, e.league_id, e.p1_name, e.p2_name, e.start_ts,",
        "    rr.round_no, rr.winner, rr.seconds, rr.finish_di, rr.fw,",
        "    (SELECT count(*) FROM round_results r2",
        "       WHERE r2.game_id = rr.game_id AND r2.round_no < rr.round_no",
        "         AND r2.winner = 1) AS score1_avant,",
        "    (SELECT count(*) FROM round_results r2",
        "       WHERE r2.game_id = rr.game_id AND r2.round_no < rr.round_no",
        "         AND r2.winner = 2) AS score2_avant",
        "FROM round_results rr",
        "JOIN events e ON e.game_id = rr.game_id",
        "WHERE e.league_id = 1252965",
        "ORDER BY e.start_ts, rr.round_no;",
      ]),
      H2("6.2 Enrichissement : ligne et cote du marché au moment de la manche"),
      Code([
        "-- Dernière cote connue du marché \"Durée du Round\" (g=1074) pour",
        "-- un round donné, juste avant le début de la manche.",
        "SELECT DISTINCT ON (game_id, round_no)",
        "    game_id, round_no, line, odds, ts_server",
        "FROM odds_snapshots",
        "WHERE g = 1074 AND round_no = :round_no AND game_id = :game_id",
        "ORDER BY game_id, round_no, ts_server DESC;",
      ]),
      H2("6.3 Agrégats glissants par combattant (calcul causal, voir 4.4)"),
      Code([
        "-- Pour un combattant et une date de référence donnés :",
        "-- durée moyenne de ses manches sur les matchs commencés AVANT cette date.",
        "SELECT avg(rr.seconds) AS duree_moyenne, count(*) AS n",
        "FROM round_results rr",
        "JOIN events e ON e.game_id = rr.game_id",
        "WHERE e.start_ts < :date_reference",
        "  AND (e.p1_name = :combattant OR e.p2_name = :combattant);",
      ]),
      H2("6.4 Découpage entraînement / validation / test"),
      Bullet("Découpage TEMPOREL, jamais aléatoire : entraînement sur les matchs les plus anciens, validation puis test sur les plus récents — sinon on \"triche\" en entraînant sur le futur."),
      Bullet("Découpage par MATCH (game_id), jamais par manche isolée : les manches d'un même match sont corrélées (momentum) ; répartir les manches d'un même match entre entraînement et test créerait une fuite indirecte."),
      Bullet("Une fois un premier modèle en place, validation en \"walk-forward\" (section 9) plutôt qu'un split figé une fois pour toutes."),

      // ================================================================ 7. MODÈLES
      H1("7. Choix de modèles"),
      H2("7.1 Mortal Kombat X — probabilité de durée"),
      Bullet("Référence de départ (baseline) : régression logistique sur les variables de la section 4.1/4.2 — simple, rapide, sert de point de comparaison minimal."),
      Bullet("Modèle principal : gradient boosting (XGBoost ou LightGBM), qui capture mieux les interactions (ex. round_no × combattant) sans ingénierie manuelle exhaustive."),
      Bullet("Calibration obligatoire (Platt scaling ou isotonic regression) : l'objectif est une PROBABILITÉ directement comparable à celle du marché, pas seulement une classe correcte la plupart du temps."),

      H2("7.2 Mortal Kombat 3 — type de finish"),
      Bullet("Approche hiérarchique recommandée compte tenu du déséquilibre des classes (section 3.2) : (a) un premier modèle binaire Fatality vs. Non-Fatality (les deux classes majoritaires, où le marché 3533 donne déjà un repère direct), puis (b) un second modèle, conditionnel, pour résoudre le type exact au sein de chaque branche."),
      Bullet("Pondération des classes rares (class_weight ou rééchantillonnage) dans tous les cas — un modèle non pondéré prédira presque toujours Regular ou Fatality et ignorera Hara-Kiri/Animality/Friendship/Babality."),
      Bullet("Alternative si le volume de classes rares reste insuffisant après plusieurs semaines (section 5) : regrouper Babality/Friendship/Animality/Hara-Kiri en une classe « finish spécial rare » pour un modèle en production plus robuste, tout en gardant un modèle de recherche séparé, plus fin, à mesure que les données rares s'accumulent."),

      // ================================================================ 8. ÉVALUATION
      H1("8. Évaluation"),
      H2("8.1 Modèle Mortal Kombat X"),
      Bullet("Brier score et log-loss (qualité de la probabilité, pas seulement de la classe)"),
      Bullet("Courbe de calibration (les manches prédites à 70 % de chances de dépasser le seuil doivent effectivement le dépasser ≈70 % du temps)"),
      Bullet("AUC, à titre secondaire"),
      Bullet("Comparaison directe à la probabilité implicite de la cote du marché (1/cote, normalisée) : le modèle n'a de valeur que s'il fait mieux, ou au moins aussi bien, avec une meilleure calibration"),

      H2("8.2 Modèle Mortal Kombat 3"),
      Bullet("Log-loss multiclasse (pénalise fortement une probabilité proche de 0 donnée à la classe qui se réalise réellement)"),
      Bullet("Matrice de confusion et rappel PAR CLASSE — l'exactitude globale (accuracy) est trompeuse ici : un modèle qui prédit toujours Regular ou Fatality aura une exactitude élevée tout en étant inutile sur les classes rares"),
      Bullet("Comparaison au marché « Mode de Victoire » (groupe 1066) sur le sous-ensemble de classes qu'il couvre (Fatality/Brutality/No Finish)"),

      // ================================================================ 9. VALIDATION
      H1("9. Protocole de validation (walk-forward)"),
      P("Plutôt qu'un unique découpage entraînement/test figé, valider en avançant dans le temps, à mesure que les données s'accumulent :"),
      Bullet("Semaine 1 : entraîner sur les jours 1 à 5, valider sur les jours 6 à 7"),
      Bullet("Semaine 2 : entraîner sur les jours 1 à 12, valider sur les jours 13 à 14"),
      Bullet("Et ainsi de suite — chaque nouvelle fenêtre de validation est toujours strictement postérieure à son entraînement"),
      P("Cette discipline détecte tôt un problème de dérive (le fournisseur change son générateur) ou de surapprentissage, plutôt que de le découvrir une fois le modèle mis en production."),

      // ================================================================ 10. CALENDRIER
      H1("10. Calendrier et feuille de route"),
      table([1600, 2600, 5800], [
        ["Phase", "Échéance", "Contenu"],
        ["1", "Continu (déjà en cours)", "La collecte tourne 24 h/24 sans interruption ; aucune action requise, seulement laisser le volume s'accumuler"],
        ["2", "Jour 3 à 7", "Extraction du premier jeu de données (section 6), modèle baseline sur les variables génériques (4.1), première comparaison au marché (8)"],
        ["3", "Semaine 2 à 3", "Ajout des variables par combattant individuel ; réentraînement ; premier bilan de calibration"],
        ["4", "Semaine 4 à 8", "Ajout des variables de matchup (paire de combattants) si le volume le permet ; regroupement des classes rares MK3 si nécessaire (7.2)"],
        ["5", "Continu ensuite", "Réentraînement hebdomadaire ; validation walk-forward (9) ; surveillance de la dérive par rapport au marché"],
      ]),

      // ================================================================ 11. INTÉGRATION
      H1("11. Intégration temps réel (aperçu — phase ultérieure)"),
      P("Hors périmètre d'entraînement proprement dit, mais pour situer la suite : une fois un modèle validé, son intégration au collecteur existant suivrait le même principe que le fil de match Telegram déjà en production (voir Memoire.md, section 23) — un nouveau module consommant les mêmes données en direct (round_results, odds_snapshots), calculant les variables à la volée, interrogeant le modèle, et publiant la prédiction (par exemple en complément du message de manche déjà envoyé). Ce module ne fait pas partie du présent plan d'entraînement et sera précisé séparément le moment venu."),

      // ================================================================ 12. RISQUES
      H1("12. Risques et limites"),
      Bullet("Nature simulée du jeu : plancher d'aléa irréductible, déjà signalé en section 1.3 — à rappeler dans toute présentation des résultats pour ne pas sur-promettre."),
      Bullet("Déséquilibre extrême des classes rares côté MK3 (Hara-Kiri, Animality) : un modèle entraîné trop tôt sur trop peu d'exemples de ces classes donnera des probabilités non fiables sur elles, même si le reste du modèle est bon."),
      Bullet("Dérive possible si le fournisseur du jeu modifie son générateur (nouveaux personnages, rééquilibrage) : le protocole walk-forward (section 9) est la principale protection, à ne jamais sauter une fois le modèle en production."),
      Bullet("Fuite de données (data leakage) : rappelée en 4.4 et 6.4 car c'est l'erreur la plus fréquente et la plus difficile à détecter a posteriori — toujours vérifier qu'aucune variable ne \"voit\" le futur par rapport à la manche prédite."),
      Bullet("Champ WT de round_results, dont la signification reste non établie (Memoire.md, section 15) : à exclure des variables tant qu'il n'est pas mieux compris, plutôt que de l'utiliser à l'aveugle."),

      // ================================================================ ANNEXE
      H1("Annexe — glossaire rapide"),
      table([2400, 6200], [
        ["Terme", "Définition"],
        ["Brier score", "Erreur quadratique moyenne entre une probabilité prédite et l'issue réelle (0 ou 1) — plus bas est meilleur."],
        ["Log-loss", "Pénalise fortement une probabilité confiante mais fausse — la métrique de référence pour des probabilités bien calibrées."],
        ["Calibration", "Une prédiction à 70 % doit se réaliser ≈70 % du temps sur un grand nombre de cas — distinct de la simple exactitude de classement."],
        ["Walk-forward", "Validation qui avance dans le temps par fenêtres successives, toujours entraînement avant, test après."],
        ["Data leakage (fuite)", "Utiliser, par erreur, une information qui ne serait pas disponible au moment réel de la prédiction — fausse la performance mesurée à la hausse."],
        ["Probabilité implicite", "1 / cote décimale (approximation avant retrait de la marge du bookmaker) — sert de repère de comparaison au modèle."],
      ]),
    ],
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("plan_entrainement_mortal_kombat.docx", buf);
  console.log("écrit : plan_entrainement_mortal_kombat.docx");
});
