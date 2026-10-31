# OpenGraph

**Arbitrage FX à chemins ouverts — venue IC Markets (cTrader).**
Méthodologie validée en backtest sur ticks réels Dukascopy · Convention A ·
univers FX pur (8 devises, 28 paires).

> Dépôt privé — propriété exclusive. Voir `NOTICE`.
>
> **Documentation complète : [`docs/`](docs/00_INDEX.md)** — stratégie et
> formules, pipeline données, modèle d'exécution, calibration, résultats,
> architecture.

---

## Statut

| Brique | État |
|---|---|
| Méthodologie (document scellé + constitution) | Figée — chemins ouverts exclusivement |
| Données POC | 28/28 paires FX, ticks ms bid/ask/volume, 3 jours (gelées) |
| Moteur de référence Python (`audit/poc_engine.py`) | R7 — fraîcheur bilatérale, rejouabilité déterministe |
| Calibrage in-sample (Optuna, 150 trials) | Terminé — marge moy. ~0,6 bp/trade, wr 99,8 %, 4 aborts |
| Moteur Rust (`engine/`) | Porté + binding pyo3 — parité bit-exacte vs POC (51 816 cands, 0 divergence) |
| Stratégie NautilusTrader 1.230 | **Parité validée** — 62 145 comparaisons identiques au replay direct |
| Réalisme (étape 5) | Ordres IOC réels, latence 0-50 ms, vol_guard par côté, file L1 — 0 flip |
| Performance (étape 6) | calc p99 61 µs, e2e p99 536 µs, gc.freeze → max 1,3 ms, 57 k t/s |
| Hold-out 27/10 (aveugle, `ddd_min=0`) | **−60,65 $** / 134 execs — jour calme, saignement sous-marginal |
| **Audit du seuil (31/10)** | Plateau `ddd_min ∈ [2,4]` sur flux venue → **retenu 2,5 $** ; holdout post-hoc **+11,25 $** / 6 execs |
| Indicateurs de régime (`regime_indicators/`) | Crate sœur testée (43 tests) : T̃, D/c⋆, hystérésis, modulation gπ — télémétrie, en aval de l'oracle |
| Adapter venue cTrader | Non démarré (runbook étape 9) |

**Pile serveur** : Ubuntu · Rust 1.99 (rustup `~/.cargo`) · `venv-og` Python
3.14 — `maturin` build de `engine/` (wheel `opengraph` cp314) +
`nautilus_trader==1.230.0` + `numpy`. Données `/opt/arb/data/<SYM>/<JOUR>/<HH>.csv`
(hors dépôt). Repro : `docs/06_ARCHITECTURE.md`.

## Architecture

```
Ticks Dukascopy (ms, bid/ask/vol)
        │
        ▼
Moteur : chemins ouverts ≤3 jambes, graphe 8 devises (2 072 chemins FX)
        │  DDD aux prix exécutables — fraîcheur bilatérale, jamais de futur
        ▼
Exécution séquentielle E1-E9 (IOC, slippage gate, partiels, unwind, DDD
recalculé par jambe)
        │
        ▼
Port Rust (`engine/`) → Strategy NautilusTrader → adapter cTrader
```

Détail couche par couche : [`docs/06_ARCHITECTURE.md`](docs/06_ARCHITECTURE.md).

Décisions structurantes :

- **Chemins ouverts uniquement** — jamais de triangles/cycles fermés (zone
  saturée par la concurrence ; voir `MC/document_scelle.txt`).
- **Convention A** — tout résiduel est marqué aux prix exécutables
  (liquidation bid/ask). Les terminaisons CFD restent hors périmètre jusqu'à
  validation opérationnelle FX (elles sont mécaniquement dominées sous A).
- **Univers strict Annexe J** — 28 paires FX (J.2) ; bouts J.3 collectés
  pour la phase ultérieure ; exclusions J.4 respectées.
- **Économie réelle** — commission 3 $/côté/100 K (taux négocié cTrader,
  6 $ AR) ; spreads réels tick par tick ; slippage et rejets modélisés.

## Contenu du dépôt

| Chemin | Rôle |
|---|---|
| `docs/` | **Documentation de référence** : stratégie + formules, données, exécution, calibration, résultats, architecture |
| `audit/` | POC Python complet : moteur (`poc_engine.py`), calibrage (`poc_optuna.py`, `poc_optuna_pen.py`), pile NT (`nt_realism.py`, `nt_realism_legs.py`), seuil (`poc_thresh_grid.py`), régime (`regime_diag.py`), statement (`gen_statement.py`), tests (`test_r7.py`, `test_pen_objective.py`), rapports (`RAPPORT_INSAMPLE.md`, `HOLDOUT_2026-10-27.md`, `REVUE_CRITIQUE.md`, `POC_PIPELINE.md`, `AUDIT_OPERATIONNEL.md`) |
| `MC/` | Documents canoniques : `document_scelle.txt`, `constitution_unifiee.txt`, inventaire Optuna |
| `Broker/` | Spécifications produit IC Markets (PDS Forex/Indices/Commodities/CFD) |
| `engine/` | Moteur Rust `opengraph` — cible de production (Convention A) + binding pyo3 |
| `regime_indicators/` | Crate sœur — tension T̃, dominance D/c⋆, hystérésis, modulation gπ |
| `backup_pdf/` | Versions pré-purge des documents canoniques (piste d'audit) |
| `AGENTS.md` | Notes opérationnelles projet (protocoles, décisions, runbook) |

Références externes non versionnées : `AlgoGraph/*.zip` (code tiers de revue).

## Reproduction

```bash
# Tests de régression moteur (serveur, données présentes)
python3 audit/test_r7.py            # T1-T6 : futur, rembobinage, rejouabilité

# Rapport in-sample détaillé (replay exec_seq instrumenté)
python3 audit/report_insample.py    # -> JSON candidats/épisodes/fills/marges
```

## Résultats clés

**Venue NautilusTrader réaliste** (IOC L1, latence 5 ms, file de queue —
chiffres qui comptent, `docs/05_RESULTATS.md` §8) :

- In-sample 22+23+26 oct. : **+47 706 $** / 11 266 execs (`ddd_min=0`) —
  le 22/10 (jour de choc) produit +48 393 $, les jours calmes saignent
- **`ddd_min = 2,5`** : in-sample **+49,6 k$** avec **zéro jour perdant** ;
  holdout 27/10 post-hoc **+11,25 $** / 6 execs (vs −60,65 $ / 134)
- Tension T̃ sépare les régimes (médiane 0,53 vs 0,20-0,28) — télémétrie,
  pas de seuil adaptatif retenu (aucun gain mesurable sur 3 jours)

Modèle rapide `exec_seq` (référence de palier, in-sample 22+23) :

- 186 103 trades simulés · **wr 99,76 %** · 4 aborts (0,002 %) · 21 jambes partielles
- Marge moyenne **~0,6 bp/trade** (≈6,1-6,8 $ / 100 K$) — bande cible 0,5-0,9 pip
- Séquence 3 jambes : **7,5 ms** vs persistance médiane des épisodes **296 ms**
  (p90 7,25 s) — la fenêtre temporelle n'est pas le goulot

Détail complet : `audit/RAPPORT_INSAMPLE.md` · `audit/HOLDOUT_2026-10-27.md`
· `docs/07_REGIME_SEUIL.md`.

## Feuille de route (runbook Solution A)

1. Object Storage Vultr (`raw/ processed/ holdout/ reports/ artifacts/`)
2. **Dépôt GitHub + squelette Rust** ← ici
3. Cœur de calcul FX Rust (Convention A, L≤3)
4. Port en Strategy NautilusTrader
5. Backtest interne sur données téléchargées
6. Tests perf/latence bloquants (<1 ms p99 calcul · <5 ms p99 e2e · ≥10 k ticks/s)
7. Hold-out jour 26 unique + PedAnova
8. Rapport PoC FX formel
9. Adapter venue cTrader + push final
