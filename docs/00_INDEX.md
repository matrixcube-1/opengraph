# OpenGraph — Documentation

Documentation de référence du projet d'arbitrage FX à chemins ouverts.
Convention A · univers FX pur · venue IC Markets.

## Plan de lecture

| # | Document | Contenu |
|---|---|---|
| 01 | [STRATEGIE.md](01_STRATEGIE.md) | Concept, graphe de devises, énumération des chemins, **formules DDD complètes**, épisodes, marges |
| 02 | [DONNEES.md](02_DONNEES.md) | Source Dukascopy, format tick, pipeline d'ingestion, couverture, sémantique des volumes |
| 03 | [EXECUTION.md](03_EXECUTION.md) | Exécution séquentielle E1-E9 : fills, partiels, rejets, unwind, abort, budget temporel |
| 04 | [CALIBRATION.md](04_CALIBRATION.md) | Campagne Optuna v2 : espace des 8 paramètres, contraintes, TPE vs hasard, PedAnova |
| 05 | [RESULTATS.md](05_RESULTATS.md) | Chiffres in-sample vérifiés : candidats, marges par paire, fills, faisabilité, limites |
| 06 | [ARCHITECTURE.md](06_ARCHITECTURE.md) | Couches POC Python → Rust `opengraph` → NautilusTrader → cTrader |
| 07 | [REGIME_SEUIL.md](07_REGIME_SEUIL.md) | **Formules régime** (T̃, D, c⋆, hystérésis, modulation) + audit du seuil `ddd_min = 2,5`, plateau, verdict holdout |

## Arborescence du dépôt

```
opengraph/
├── README.md                        vue d'ensemble + statut + feuille de route
├── NOTICE                           propriété exclusive (pas de licence)
├── SECURITY.md                      politique secrets / signalement
├── AGENTS.md                        notes opérationnelles (protocoles, décisions)
│
├── docs/                            ← cette documentation
│   ├── 00_INDEX.md                  navigation + arborescence
│   ├── 01_STRATEGIE.md              stratégie + formules
│   ├── 02_DONNEES.md                pipeline données
│   ├── 03_EXECUTION.md              modèle d'exécution E1-E9
│   ├── 04_CALIBRATION.md            Optuna v2 + PedAnova
│   └── 05_RESULTATS.md              chiffres vérifiés + limites
│
├── MC/                              documents canoniques (source méthodo)
│   ├── document_scelle.txt          méthodologie scellée (post-purge R5)
│   ├── constitution_unifiee.txt     constitution extraite
│   ├── optuna-inventaire-enrichi.md référence des patterns Optuna
│   └── Simulation_MonteCarlo_*.pdf  validation Monte Carlo
│
├── Broker/                          PDS IC Markets (Forex, Indices, CFD, ticks)
│
├── audit/                           POC Python de référence + pistes d'audit
│   ├── poc_engine.py                moteur : quote state, eval_at, exec_seq, scan
│   ├── poc_optuna.py                replay, objectif, contraintes, holdout
│   ├── poc_optuna_pen.py            re-tuning : contrainte no_losing_day
│   ├── poc_thresh_grid.py           balayage du seuil sur flux venue (exact)
│   ├── nt_realism.py                pile NautilusTrader : modes internal/real
│   ├── nt_realism_legs.py           variante instrumentée (jambes + --ddd-min)
│   ├── regime_diag.py               flux régime T̃/D/c⋆ sur ticks réels
│   ├── gen_statement.py             relevé d'exécutions (PDF + CSV)
│   ├── report_insample.py           rapport détaillé (replay instrumenté)
│   ├── decompose_trade.py           réconciliation jambe par jambe
│   ├── test_r7.py                   régressions T1-T6 (futur, rembobinage, replay)
│   ├── test_pen_objective.py        tests helpers objectif pénalisé
│   ├── data_structure.py            audit de couverture par symbole/jour
│   ├── fetch_poc.py                 downloader .bi5 (ledger, WAF-safe)
│   ├── statement_flow.pdf           relevé broker 8 p. (11 400 execs)
│   ├── statement_trades.csv         flux détaillé des exécutions
│   ├── HOLDOUT_2026-10-27.md        holdout officiel + addendum ddd_min=2,5
│   ├── RAPPORT_INSAMPLE.md          rapport intermédiaire consolidé
│   ├── CORRECTIONS_LOG.md           journal R6/R7 (bugs d'état corrigés)
│   ├── REVUE_CRITIQUE.md            revue critique méthodologie
│   ├── POC_PIPELINE.md              procédures serveur
│   └── *.png                        figures de revue
│
├── engine/                          moteur Rust opengraph — cible production
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs                   invariants méthodologiques
│       ├── universe.rs              tables Annexe J (CURR, FX) — ordre fidèle
│       ├── paths.rs                 enum_paths() + tables précompilées
│       ├── quote.rs                 Quote {ts_ms, bid, ask, vol}
│       ├── fees.rs                  COMM 3,00 · NOTIONAL · abort cost
│       ├── data.rs                  loader CSV /opt/arb/data
│       ├── engine.rs                état rembobinable, jamais de futur
│       ├── eval.rs                  DDD bid/ask + fraîcheur + spread_cap
│       ├── exec.rs                  E1-E9, IOC, partiels, garde volume
│       ├── scan.rs                  scan événementiel + replay (cooldown)
│       └── pybind.rs                binding pyo3 (eval_at/exec_seq/settle)
│
├── regime_indicators/               crate sœur — indicateurs de régime
│   └── src/
│       ├── tension.rs               résidu de Hodge, Ā+A²+A³, Frobenius
│       ├── svd_regime.rs            dominance D, c⋆ (centré/brut corrigé)
│       ├── smoothing.rs             EWMA + médiane/MAD robuste
│       ├── gate.rs                  porte à hystérésis (fail-closed)
│       ├── snapshot.rs              instantané publié (ArcSwap)
│       └── engine.rs                orchestrateur boucle lente ~3 µs
│
└── backup_pdf/                      versions pré-purge (piste d'audit)
```

## Glossaire minimal

| Terme | Sens |
|---|---|
| Chemin ouvert | suite de 1-3 conversions de devises sans retour au point de départ — **jamais** de cycle fermé |
| DDD | « drop-dead differential » : écart net exécutable d'un chemin à un instant |
| Convention A | marquage des résiduels aux prix exécutables (liquidation bid/ask) |
| Épisode | fenêtre contiguë où le meilleur net global reste > 0 (persistance du gap) |
| `fresh_ms` | âge maximal toléré d'une quote pour la valider |
| `lat_ms` | latence modélisée entre envoi d'ordre et fill, par jambe |
| `ddd_min` | marge nette minimale exigée pour déclencher l'envoi — **2,5 $** |
| `tau_slip_bp` | mouvement de prix toléré entre quote d'envoi et quote de fill |
| `cooldown_s` | verrou anti-rechevauchement après une exécution |
| `T̃` | tension multi-sauts : incohérence globale des mids normalisée par les spreads (régime) |
| `D` | dominance spectrale σ₁²/Σσₖ² des rendements paniers devises |
| `c⋆` | devise dominante du facteur σ₁ (si chargement > q_svdmin) |
| `t_free` | verrou de sérialisation : aucun envoi avant `t + dur + cooldown` |
