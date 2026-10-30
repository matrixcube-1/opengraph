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
| Moteur Rust (`engine/`) | Porté + binding pyo3 — 29 tests, parité bit-exacte vs POC (51 816 cands, 0 divergence) |
| Stratégie NautilusTrader 1.230 | **Parité validée** — 62 145 comparaisons identiques au replay direct |
| Réalisme (étape 5) | Ordres IOC réels, latence 0-50 ms, vol_guard par côté, file L1 — 0 flip ; **pire cas +47 338 $/3 j** |
| Performance (étape 6) | calc p99 61 µs, e2e p99 536 µs, gc.freeze → max 1,3 ms, 57 k t/s |
| Hold-out jour non vu | **Téléchargement en cours** (2026-10-27) — test unique lundi, pile NT figée |
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
| `audit/` | POC Python complet : moteur (`poc_engine.py`), calibrage (`poc_optuna.py`), tests de régression (`test_r7.py`), rapports (`RAPPORT_INSAMPLE.md`, `CORRECTIONS_LOG.md`, `REVUE_CRITIQUE.md`, `POC_PIPELINE.md`, `AUDIT_OPERATIONNEL.md`) |
| `MC/` | Documents canoniques : `document_scelle.txt`, `constitution_unifiee.txt`, inventaire Optuna |
| `Broker/` | Spécifications produit IC Markets (PDS Forex/Indices/Commodities/CFD) |
| `engine/` | Moteur Rust — squelette, cible de production (Convention A) |
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

## Résultats clés (in-sample, 22+23 oct. 2026)

- 186 103 trades simulés · **wr 99,76 %** · 4 aborts (0,002 %) · 21 jambes partielles
- Marge moyenne **~0,6 bp/trade** (≈6,1-6,8 $ / 100 K$) — bande cible 0,5-0,9 pip
- Séquence 3 jambes : **7,5 ms** vs persistance médiane des épisodes **296 ms**
  (p90 7,25 s) — la fenêtre temporelle n'est pas le goulot
- Convergence calibrée : latence 1,3-2,5 ms · fraîcheur 0,9-1,2 s · ddd_min 1,7-3,6 $

Détail complet : `audit/RAPPORT_INSAMPLE.md`.

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
