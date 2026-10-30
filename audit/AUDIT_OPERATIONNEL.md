# AUDIT OPÉRATIONNEL — Pipeline de calibration Optuna

Date : 2026-10-25 · Statut : pré-déploiement (en attente accès serveur Linux)
Sources : Constitution v2.2, Document scellé (corrigés), Disponibilite_Ticks_Dukascopy,
optuna-inventaire-complet.md, REVUE_CRITIQUE.md

Verdict : la documentation spécifie correctement **les formules, les paramètres et le plan
de données** ; ce qui manque pour être opérationnel à 100 % est concentré dans
**5 modèles absents** (§4) et **3 validations de terrain** (§6). Aucune lacune
arithmétique restante après les corrections du 25/10.

---

## 1. Univers de téléchargement — liste exacte (60 symboles)

Périmètre acté (Scellé, Annexe J) : univers de calibrage = U1-N **moins** les
instruments absents de Dukascopy. Total : **28 arêtes FX + 32 bouts = 60 symboles**.
Fenêtre : 4 ans (2022-10 → 2026-10).

### 1.1 Arêtes FX K8 — 28 paires (100 % disponibles, historique > 19 ans)

Symboles Dukascopy identiques aux symboles IC Markets :

```
EURUSD  GBPUSD  USDJPY  USDCHF  AUDUSD  USDCAD  NZDUSD  EURGBP
EURJPY  EURCHF  EURAUD  EURCAD  EURNZD  GBPJPY  GBPCHF  GBPAUD
GBPCAD  GBPNZD  AUDJPY  AUDCHF  AUDCAD  AUDNZD  CADJPY  CADCHF
CHFJPY  NZDJPY  NZDCHF  NZDCAD
```

Diviseur de prix : ×100 000 (×1 000 pour les paires `*JPY`).

### 1.2 Bouts — 32/34 utilisables (26 oui + 6 partiels)

| Bout IC | Devise | Tier | Symbole Dukascopy | Début | Condition |
|---|---|---|---|---|---|
| DE40 | EUR | S | DEUIDXEUR | 2013-01 | — |
| IT40 | EUR | L | ITAIDXEUR | 2016-07 | — |
| F40 | EUR | L | FRAIDXEUR | 2013-01 | alias FR40 |
| STOXX50 | EUR | X* | EUSIDXEUR | 2014-06 | tier X mesuré, maintenu en recherche |
| NETH25 | EUR | L | NLDIDXEUR | 2017-12 | — |
| XAUEUR | EUR | L | XAUEUR | à vérifier | début réel à confirmer au pull |
| XAGEUR | EUR | L | XAGEUR | à vérifier | idem |
| UK100 | GBP | L | GBRIDXGBP | 2013-01 | FTSE 100 |
| XAUGBP | GBP | L | XAUGBP | à vérifier | début réel à confirmer |
| JP225 | JPY | L | JPNIDXJPY | 2013-01 | « Japan 225 » |
| XAUJPY | JPY | L | XAUJPY | **2024-09** | **fenêtre < 4 ans** — calibrage restreint |
| XAUCHF | CHF | L | XAUCHF | à vérifier | début réel à confirmer |
| AUS200 | AUD | L | AUSIDXAUD | 2014-06 | alias AU200 |
| XAUAUD | AUD | L | XAUAUD | à vérifier | début réel à confirmer |
| XAGAUD | AUD | L | XAGAUD | à vérifier | début réel à confirmer |
| US30 | USD | S | USA30IDXUSD | 2013-01 | natif |
| US500 | USD | S | USA500IDXUSD | 2012-01 | natif |
| US2000 | USD | L | USSC2000IDXUSD | à vérifier | début réel à confirmer |
| USTEC | USD | L | USATECHIDXUSD | 2013-01 | alias NAS100 |
| SWI20 | USD | L | CHEIDXCHF | 2013-01 | **Duka coté CHF ↔ IC coté USD** : conversion USDCHF obligatoire |
| XAUUSD | USD | S | XAUUSD | 2003-05 | natif, DIV_1000 |
| XAGUSD | USD | L | XAGUSD | 2003-05 | natif, DIV_1000 |
| XTIUSD | USD | S | LIGHTCMDUSD | 2013-01 | WTI ; trous de séance à masquer |
| XBRUSD | USD | S | BRENTCMDUSD | 2010-12 | Brent |
| XPTUSD | USD | L | XPTCMDUSD | à vérifier | symbole distinct |
| XPDUSD | USD | L | XPDCMDUSD | à vérifier | symbole distinct |
| SUGAR | USD | L | SUGARCMDUSD | 2017-10 | — |
| COCOA | USD | L | COCOACMDUSD | 2017-10 | — |
| COFFEE | USD | L | COFFEECMDUSX | 2017-12 | **cotation USX (cents) : ÷100** |
| COTTON | USD | L | COTTONCMDUSX | 2017-10 | ÷100 |
| SOYBEAN | USD | L | SOYBEANCMDUSX | 2017-12 | ÷100 |
| OJ | USD | L | OJUICECMDUSX | 2017-10 | ÷100 |

### 1.3 Exclusions actées

| Instrument | Motif |
|---|---|
| WHEAT, CORN | absents du flux Dukascopy → **retirés du graphe de calibrage** (source alternative requise pour les réintégrer) |
| ES35, CHINA50, XNGUSD | tier X surveillance → non envoyés, non calibrés |
| CA60, TecDE30, MidDE50, CHINAH, NOR25, SE30 | absents de Dukascopy |
| HK50, SA40 + USDHKD/NOK/SEK/ZAR | feuilles U2 — dénombrés pour borne seulement |
| ES, NQ, YM, GC (+ micros) | futures CME — hors Dukascopy, autre vendor |
| COPPER | présent chez Duka, hors catalogue IC |

### 1.4 Volume et stockage (estimation)

| Poste | Estimation |
|---|---|
| .bi5 bruts (60 symboles × ~48 mois) | 60–150 Go (EURUSD ~10-15 Go seul ; bouts plus minces) |
| Parquet converti (snappy, ts ms int64 + 4 float32) | ~30–60 Go |
| Événements DDD + résultats studies | < 5 Go |
| **Prévoir** | **200 Go** de marge confortable |

Points de vigilance format : mois **0-indexé** dans l'URL .bi5 ; timestamps ms UTC ;
`askVolume`/`bidVolume` = lots déclarés (proxy BIR, pas profondeur) ; trous de séance
CFD à **masquer, jamais interpoler** (règle J.5) ; diviseurs : 100 000 FX hors JPY,
1 000 *JPY et CFD, ÷100 supplémentaire pour `*CMDUSX`.

---

## 2. Chaîne de traitement — statut par étage

| # | Étage | Spécification existante | Statut |
|---|---|---|---|
| E1 | Téléchargement .bi5 (dukascopy-node, batches, retries) | Annexe J + doc dukascopy-node | **Spécifié — à scripter** |
| E2 | Conversion parquet + contrôle couverture par symbole/mois | J.5 (« contrôle préalable ») | **Spécifié — à scripter** |
| E3 | Alignement multi-symboles (état commun, asof) | **absent du corpus** | **À concevoir — critique** |
| E4 | Détection événements DDD_eff > Coût_total + mesure τ | §7-8 (formules OK) | Spécifié — dépend de E3 |
| E5 | Estimation structurelle : Hawkes MLE, Hurst, spreads, η | §10, §17, Annexe J | Spécifié — MLE dédié décidé |
| E6 | Study Optuna : seuils de décision | Annexe J + inventaire | **Objective à formaliser** (en cours) |
| E7 | Validation : walk-forward, holdout, Cox-Snell | §15, Annexe J | Spécifié — à implémenter |

Le goulot n'est pas Optuna : c'est **E3** (protocole d'alignement de ticks
asynchrones — condition d'existence de tout DDD) puis **E6** (définition de
l'objective).

---

## 3. Matrice des formules — état opérationnel

Précédemment auditées : ~40/42 exactes (REVUE_CRITIQUE, Annexe 0). Reclassées
selon ce qu'il faut pour *exécuter* :

### 3.1 Prêtes telles quelles (implémenter directement)

- DDD = Q_c·P_c − Q_s·P_s ; DDD unitaire ; Score ; Solde
- Bellman-Ford w = −log r ; équivalence max Πr ⇔ min Σ−log r
- Dénombrements L≤3/L≤4 (oracle d'énumération pour tester tout moteur)
- S(h) = e^{−∫λ} ; Hawkes λ = μ + Σαe^{−βΔt} (α/β < 1)
- Ĥ par OLS log N (R/S)
- BIR_aj = BIR·e^{−γ·Spread/P_mid}
- Kelly : f* = κ·μ_r/σ_r² ; x_max = κ·(μ_USD/σ²_USD)·W₀
- ES Rockafellar-Uryasev ; Herfindahl ; LP HiGHS
- p_h = 1−(1−p_an)^(h/1an) ; DDD_eff,CFD ; écart CFD/future cost-of-carry
- Coût OTC 3×(3,50+3,81)=21,93 ; comm normalisée 3,50/P_base ; spread bout
  (Ask−Bid)/P_mid×100 000 ; P_S = Spread×100 000/8,07 **(corrigée)**
- Provision portage ; swaps ; financement indices ; ajust_roll ; ajust_div ;
  provision_conv ; écart_rel métaux

### 3.2 Correctes mais dépendant d'un choix/jalon non fourni

| Formule | Ce qui manque pour l'exécuter |
|---|---|
| DDD_eff = DDD_th − ησQ√L | η non identifiable sans profondeur/fills ; exposant de Q (1 ou 0,5) à trancher en calibration (§17 le prévoit) |
| Règle d'envoi complète | nécessite p_persist, latence venue, ρ_min, calendrier annonces — tous non fournis dans les données |
| p_persist = S·1{Ĥ>0,55}·max(0,BIR_aj) | agrégation BIR par chemin non spécifiée ; sur IC Markets sans profondeur, BIR omis (règle 22) → garder les 2 variantes |
| Optimal stopping V | schématique, déclaré non implémentable (D-10) → politique de sortie du résiduel à concevoir |
| Impact / glissement résiduel | séparation impact (dans DDD_eff) vs glissement résiduel (dans Coût) : convention corrigée, mais le terme résiduel n'a pas de modèle |

### 3.3 Absentes du corpus — à concevoir (les vrais trous)

| # | Modèle manquant | Pourquoi bloquant |
|---|---|---|
| M1 | **Fraîcheur de quote par jambe** : à quel staleness max chaque bid/ask d'une jambe est-il encore valide au moment du fill | sans elle, ticks asynchrones → DDD fantômes (C2) |
| M2 | **Probabilité de fill / rejet par jambe** (requote, last-look) | le PnL simulé suppose fill à l'ask/bid affiché ; surestime systématiquement |
| M3 | **Coût de sortie du résiduel** (~1 jambe + ½-spread) | omis de Coût_total ; quasiment tout le net canonique 8,07 USD (C1) |
| M4 | **Politique d'unwind** : si jambe k remplie et k+1 échoue → coût de dénouement provisionné | exécution non atomique à 3-4 fills sans filet |
| M5 | **Budget h_exec décomposé** : h = Σ(fill_i + queue_i) par jambe, pas h monolithique | S(h+lat) agrégée ne voit pas la mort de l'edge entre fills |
| M6 | **μ pour Kelly** : procédure d'estimation absente | LP infaisable si x_max<0 ; sizing aveugle |
| M7 | **Étalonnage venue** Dukascopy → IC Markets : mapping spread/événements | seuils calibrés Duka transférés avec biais inconnu |
| M8 | **Calendrier d'annonces** (règle 9 : 10 s) : source non définie | filtre invérifiable en simulation |

---

## 4. Matrice des paramètres (§17 complété)

| Paramètre | Estimation | Optuna ? | Valeur doc | Statut |
|---|---|---|---|---|
| μ, α, β Hawkes | MLE (EM/L-BFGS-B) sur chocs DDD, valid. 4 mois/2 mois + Cox-Snell | **Non — MLE dédié** | — | décidé : MLE |
| Seuil S(h+lat) | — | **Oui** `float(0.10–0.70)` | 0,30 | Optuna |
| Ĥ seuil + fenêtre R/S | test t sur fenêtre | Oui `float(0.50–0.70)` + `int` lookback | 0,55 | Optuna |
| γ (BIR) | — | Oui `log(1–50)` | 10 | Optuna |
| η impact + exposant Q | régression glissement ~ Q×√L | **Non — externe** ; Optuna peut choisir l'exposant en catégoriel | — | bloqué profondeur |
| κ Kelly | gouvernance | Oui `float(0.15–0.50)` | 0,25–0,50 | Optuna (borne) |
| Seuil p_persist | — | Oui `float(0.20–0.60)` | 0,40 | Optuna |
| Spread cap | — | Oui `float(1.3–2.5)` | 1,7× | Optuna |
| Fenêtre annonce 10 s | calendrier externe | non | 10 s | **bloqué source** |
| Latence max | mesure continue | non | 50 ms/1 ms | mesure serveur |
| h (Cox) | délai exec moyen +50 % | dérivé mesure | — | mesure serveur |
| Spread OTC live | relevé paire/heure | non | 3,81 réf. | mesure IC |
| Coûts bouts | barème + relevés | partiel (tier seuils 8,07/30) | Tableau 2.3 | mixte |
| Frais CME | relevé FCM | non | 1,89 hist. | **hors périmètre Duka** |
| p_an, LGD | notation externe | non | 0,1–1 %/an, 40–70 % | externe |
| ρ_min, marges, W₀ | gouvernance | non | 300 %, Tableau 12.1 | fixé |
| w_roll | relevé broker | non | 23:55→00:10 | fixé |
| δ_conv | écart conversion | non | 0,5 % | relevé IC |
| Tiers bouts, swap points, RFR, courbe énergies, dividendes | plateforme/externe | non | divers | feeds quotidiens |

**≈ 9 paramètres réellement Optuna-ables** ; le reste = MLE, relevés externes ou
gouvernance. C'est conforme à l'architecture décidée : Optuna n'optimise que les
seuils de décision.

---

## 5. Objectif Optuna — cadre retenu (design en cours)

Décisions utilisateur : (1) PnL réalisable max = but final ; (2) les deux grandeurs
clés = durée de vie de l'opportunité et temps d'exécution des ≤4 fills ;
(3) MLE dédié pour Hawkes, Optuna sur les seuils ; (4) infra ~1 ms/jitter <10 µs
→ **h_exec total ≈ 4–6 ms** (vs 50 ms du doc — la fenêtre de survie se resserre
d'un ordre de grandeur).

Formulation proposée (à finaliser avec l'utilisateur) :

- **Objectif** : PnL simulé des événements envoyés = Σ_i [DDD_i(t_i+fill) −
  Coût_total_i − Coût_sortie_résiduel_i] sur les événements passant la règle
  d'envoi, chaque jambe prise au **prix exécutable défavorable** (ask/bid, pas
  mid) pendant sa fenêtre de fill — M3/M5 inclus.
- **Multi-objectif** : max PnL simulé / min taux de faux positifs (envoyé mais
  DDD résorbé < h) / min MaxDD proxy. `NSGAIISampler`, `set_constraint` pour
  marge et volume min d'événements.
- **Walk-forward** : 4 ans → folds temporels avec embargo (événements qui
  chevauchent la frontière) ; `trial.report(score_fold, fold)` +
  `SuccessiveHalvingPruner` ; holdout dernier trimestre **une seule fois**.
- **Warm-start** : `enqueue_trial` avec les valeurs canoniques
  (0,30 / 0,55 / 10 / 1,7 / 0,40 / 0,25).
- **Stockage** : RDB SQLite → Postgres si workers parallèles ; Dashboard ;
  artifacts (equity curve, event log par trial).
- **Seed fixe**, version données loguée, `study.set_metric_names`.

---

## 6. Validations de terrain restantes (avant lancement study)

| # | Validation | Bloquant ? |
|---|---|---|
| V1 | Pull réel d'au moins 1 mois × 5 symboles (le test .bi5 live était non concluant : 503/timeout) | **Oui — prouve la faisabilité réseau** |
| V2 | Couverture effective 4 ans des dates « à vérifier » (métaux croisés, US2000, XPT/XPD) | Oui (J.5 le prescrit) |
| V3 | Protocole E3 chiffré : staleness tolérée par jambe, densité de ticks par symbole/heure (croisés minés la nuit ?) | **Oui** |
| V4 | Échantillon ticks IC Markets réels (même 2-3 jours) pour amorcer M7 | Fortement recommandé |
| V5 | Version Optuna installée vs API v5 de l'inventaire | Oui (vérifier au setup) |
| V6 | Source calendrier annonces (règle 9) | Oui pour la fidélité du filtre |
| V7 | p_an/LGD : source externe pour IC Markets (notation/compte ségrégué) | Requis pour bouts CFD |

---

## 7. Stack serveur — prérequis

```
Python 3.11+ ; numpy, pandas/polars, pyarrow (parquet), duckdb (requêtes
tick), optuna (+optuna-dashboard), numba/cython pour la détection DDD
(60 symboles × ~10⁸ ticks), scipy (MLE Hawkes L-BFGS-B), matplotlib/plotly.
Node.js + dukascopy-node (ou duka-data Python) pour le pull .bi5.
SQLite natif suffit pour la study ; Postgres si multi-workers.
200 Go disque libres ; jobs réseau isolables (le pull 4 ans durera des heures/jours).
```

Reproductibilité : seeds fixes, hashes de fichiers sources logués, config par
study versionnée, artifacts par trial.

---

## 8. Checklist « 100 % opérationnel »

- [ ] Accès SSH serveur + stack installée (V5)
- [ ] Pull de validation V1 réussi
- [ ] 60 symboles × 4 ans téléchargés + contrôle couverture V2
- [ ] Protocole d'alignement E3 spécifié et chiffré (staleness, asof) — V3
- [ ] Détecteur événements DDD + mesure τ sur fenêtre pilote
- [ ] MLE Hawkes + validation Cox-Snell sur fold 4 mois/2 mois
- [ ] Modèles M1–M5 codés (même conservateurs) dans l'objective
- [ ] Study Optuna lancée (warm-start, walk-forward, multi-obj)
- [ ] Source annonces + p_an/LGD + échantillon IC Markets (V4, V6, V7)
- [ ] Holdout intact jusqu'à sélection finale
