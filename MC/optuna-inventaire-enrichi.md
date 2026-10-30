# Inventaire complet Optuna — HPO & calibration (algorithmes, ML, quant)

> Document de référence bout-en-bout, vérifié contre la documentation officielle Optuna (**stable / v5.x**), Optuna Dashboard, OptunaHub et optuna-integration — **2026-10-26** (enrichi).  
> Prose en français · noms d’API Optuna en anglais · exemples Python auto-contenus.  
> **Aucune API inventée.** Capacités absentes des sources officielles omises ou marquées « à vérifier sur la doc stable ».
>
> Sources d’enrichissement : `optuna.readthedocs.io/en/stable/` (API + tutorials CLI / RDB / ask-and-tell), release note GitHub `v5.0.0`, `hub.optuna.org` (sitemap + fiches packages), `optuna-integration.readthedocs.io`, `optuna-dashboard.readthedocs.io`.

**Caveat quant / général :** Optuna optimise l’*objective* que **vous** définissez. Ce n’est pas un calibrateur MLE/EM dédié, ni un solveur MIP/convex, ni un risk engine, ni une garantie anti-overfitting. Un `best_trial` in-sample n’est **pas** une edge live ni une généralisation OOS. Les pruners coupent des évaluations chères — ils ne mesurent pas le sur-apprentissage.

---

## Changelog d’enrichissement (2026-10-26)

| Ajout / expansion | Contenu |
|---|---|
| Carte mentale « spectre complet » | Study → Sampler → Pruner → Storage → Viz → Dashboard → Hub → Integration |
| Arbre de décision / matrice | Type d’algo → sampler + pruner + storage recommandés |
| Cheat-sheet hyperparams | Params clés vérifiés (TPE, CMA-ES, GP, NSGA-II, Median, Hyperband, SHA, …) |
| CLI Optuna | Commandes officielles `ask` / `tell` / `create-study` / … |
| OptunaHub inventaire structuré | Catégories + packages notables (pas 313 stubs) + `optunahub.load_module` |
| Exceptions | `TrialPruned`, `DuplicatedStudyError`, `UpdateFinishedTrialError`, … |
| Heartbeat / `fail_stale_trials` | Recette RDB complète + `RetryHeartbeatStaleTrialCallback` |
| gRPC storage proxy | Esquisse serveur/client (`run_grpc_proxy_server`, `GrpcStorageProxy`) |
| Search space approfondi | `IntersectionSearchSpace`, pièges espace dynamique / noms suggest |
| Patterns HPO génériques | sklearn/boosting, DL+pruning, black-box numérique, nested/conditional, warm-start, batch ask/tell |
| Ce qu’il ne faut **pas** optimiser avec Optuna | MLE convex, MIP, gradients connus, etc. |
| Références | Liens Hub, CLI, exceptions, storages heartbeat/gRPC |
| Artefacts / logging | Table stores officiels + download meta ; API `optuna.logging.*` |
| Hub élargi | CatCMA, HEBO, TuRBO, MO-CMA, SPEA-II, PSO, meta-learn TPE, SMAC, … |
| Élargissement hors FX | Même esprit anti-overfitting ; patterns applicables à tout algorithme black-box |

---
## Table des matières

0. [Carte mentale — spectre complet Optuna](#0-carte-mentale--spectre-complet-optuna)
1. [Préambule — ce qu’est Optuna / ce que ce n’est pas](#1-préambule--ce-quest-optuna--ce-que-ce-nest-pas)
2. [Installation & packages](#2-installation--packages)
3. [Anatomie Study / Trial / Objective](#3-anatomie-study--trial--objective)
4. [API ask/tell, enqueue_trial, frozen trials](#4-api-asktell-enqueue_trial-frozen-trials)
5. [Espace de recherche (search space)](#5-espace-de-recherche-search-space)
6. [Samplers — inventaire complet](#6-samplers--inventaire-complet)
7. [Pruners — inventaire + backtest / early-stop](#7-pruners--inventaire--backtest--early-stop)
8. [Multi-objectif, contraintes & Pareto](#8-multi-objectif-contraintes--pareto)
9. [Importance des hyperparamètres](#9-importance-des-hyperparamètres)
10. [Storage & parallélisme](#10-storage--parallélisme)
11. [Callbacks, logging, artefacts, terminator, HITL](#11-callbacks-logging-artefacts-terminator-hitl)
12. [Visualisation (`plot_*`)](#12-visualisation-plot_)
13. [Optuna Dashboard](#13-optuna-dashboard)
14. [Intégrations (`optuna-integration`)](#14-intégrations-optuna-integration)
15. [Patterns quant finance profonds](#15-patterns-quant-finance-profonds)
16. [Checklist anti-overfitting](#16-checklist-anti-overfitting)
17. [Arbre de décision — choisir la stack](#17-arbre-de-décision--choisir-la-stack)
18. [Cheat-sheet hyperparams samplers & pruners](#18-cheat-sheet-hyperparams-samplers--pruners)
19. [CLI Optuna](#19-cli-optuna)
20. [OptunaHub — inventaire structuré](#20-optunahub--inventaire-structuré)
21. [Exceptions utiles](#21-exceptions-utiles)
22. [Heartbeat, stale trials & gRPC proxy](#22-heartbeat-stale-trials--grpc-proxy)
23. [Patterns HPO génériques (ML / numérique / black-box)](#23-patterns-hpo-génériques-ml--numérique--black-box)
24. [Ce qu’il ne faut **pas** optimiser avec Optuna](#24-ce-quil-ne-faut-pas-optimiser-avec-optuna)
25. [Références officielles](#25-références-officielles)

---
## 0. Carte mentale — spectre complet Optuna

Vue agent : chaque couche est interchangeable ; la qualité du résultat dépend du **couplage** sampler ↔ espace ↔ budget ↔ objective (et de la CV / coûts que **vous** codez).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         OBJECTIVE (vous)                                │
│  métrique black-box : loss CV, Sharpe, RMSE calib, latency, …           │
│  + trial.suggest_*  + report/should_prune  + set_constraint  + attrs    │
└─────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────┴───────────────┐
│  STUDY  (create_study / load) │  direction(s) · study_name · load_if_exists
└───────────────┬───────────────┘
     ┌──────────┼──────────┬──────────────┬────────────────┐
     ▼          ▼          ▼              ▼                ▼
 SAMPLER     PRUNER     STORAGE      CALLBACKS/TERM     ARTIFACTS
 TPE/CMA/    Median/    InMemory/    MaxTrialsCb        FileSystem/
 GP/NSGA/    Hyperband/ SQLite/      logging            Boto3/GCS
 Grid/Auto   SHA/Nop    Postgres/    Hub terminator
 (Hub…)      (Hub…)     Journal/gRPC
     │          │          │
     └──────────┴────┬─────┘
                     ▼
              TRIALS history
         best_trial / best_trials (Pareto)
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   visualization  importance   DASHBOARD
   plot_*         PedAnova     HITL / preferential / LLM / notes
        │
        ▼
   OPTUNAHUB          OPTUNA-INTEGRATION
   samplers/pruners/  LightGBM/XGB/sklearn/BoTorch/
   viz/benchmarks/    Lightning/Keras/MLflow/W&B/Dask…
   callbacks
```

| Couche | Question agent | Défauts v5 (core) |
|---|---|---|
| Sampler | Espace mixte / continu / multi-obj / budget ? | `TPESampler` (aussi défaut **multi-obj** depuis v5) |
| Pruner | Évaluations progressives (epochs, folds) ? | `MedianPruner` ; **mono-objectif seulement** |
| Storage | Local / overnight / multi-node ? | `None` → InMemory ; sinon URL RDB |
| Importance | Quels knobs réduire ? | `PedAnovaImportanceEvaluator` (défaut v5) |
| Contraintes | Faisabilité hard ? | `trial.set_constraint` (≤ 0 = OK) ; `constraints_func` déprécié → v7 |
| Hub | Besoin AutoSampler / CatCMA / terminator ? | `optunahub.load_module(...)` |

---
### Index recettes (où copier-coller)

| Besoin | Section |
|---|---|
| Choisir sampler/pruner/storage | §17 matrice + §18 cheat-sheet |
| Ask/tell batch, warm-start | §4, §23.5–23.6 |
| Espace conditionnel / intersection | §5 |
| Pruning backtest / epochs | §7, §23.1–23.2 |
| Multi-obj + contraintes ≤ 0 | §8 |
| Overnight RDB + workers | §10, §15.6 |
| Heartbeat zombies / gRPC | §22 |
| CLI lab interactif | §19 |
| AutoSampler / CatCMA / terminator Hub | §20 |
| Quant FX / calib Heston-OU-GARCH | §15 |
| Anti-overfitting gate | §16 |
| Ne pas utiliser Optuna | §24 |


## 1. Préambule — ce qu’est Optuna / ce que ce n’est pas

### Concept

**Optuna** est un framework d’optimisation d’hyperparamètres (HPO) *black-box* : une **Study** orchestre des **Trials** ; un **Sampler** propose des paramètres ; un **Pruner** (mono-objectif) coupe tôt les essais médiocres ; un **Storage** persiste l’historique ; la visualisation / le Dashboard analysent.

Flux typique :

```
create_study(direction|directions, sampler, pruner, storage)
    → optimize(objective)  OU  ask → suggest_* → tell
    → report / should_prune  ·  set_constraint  ·  artefacts
    → plot_* / Dashboard / importance / best_trial(s)
```

### Optuna **est**

| Capacité | API / couche |
|---|---|
| Abstraction Study/Trial define-by-run | `create_study`, `Trial.suggest_*` |
| Samplers SOTA + pruning + multi-obj | `optuna.samplers.*`, `optuna.pruners.*` |
| Storage distribué | SQLite → Postgres → Journal → gRPC |
| Dashboard, artefacts, importance | `optuna-dashboard`, `optuna.artifacts`, `optuna.importance` |
| Ask/tell pour batch / HITL / boucles custom | `Study.ask` / `Study.tell` |

### Optuna **n’est pas**

| Ce n’est pas | Conséquence quant |
|---|---|
| Un solver de calibration paramétrique (MLE, EM, adjoints) | Vous codez la vraisemblance / RMSE dans l’objective |
| Un solveur convex / MIP / programmation linéaire | Préférer CVXPY, Gurobi, HiGHS, etc. (voir §24) |
| Un moteur de backtest / risque / training loop | Vous apportez simulateur, trainer, métriques |
| Une garantie anti-overfitting | Walk-forward, purged CV, hold-out / test final = **votre** responsabilité |
| Un pruner multi-objectif (core) | `pruners` = **single-objective only** (doc officielle) ; contournement Hub `multi_metric_pruner` |
| Le Dashboard qui « optimise » | Il visualise / HITL ; les workers font `optimize` |

### Quand l’utiliser (algorithmes en général + quant)

- HPO de modèles ML (boosting, réseaux, pipelines sklearn) et de stratégies (lookback, z-score, stops, sessions).
- Calibration *black-box* de modèles stochastiques / numériques (Heston, OU, GARCH, ODE/PDE paramétrées) via erreur / −loglik / RMSE.
- Multi-objectif (ex. accuracy vs latency, Sharpe × MaxDD).
- Campagnes overnight parallèles avec persistance RDB + Dashboard.
- Toute objective coûteuse sans gradient fiable, avec espace mixte (float/int/cat) ou conditionnel.

### Pièges

- Confondre `best_params` in-sample et paramètres live.
- Trop de dimensions → curse of dimensionality (doc Optuna).
- Réutiliser le jeu de test dans l’objective.
- Pruner sur une métrique non monotone / non comparable entre trials.

---

## 2. Installation & packages

### Concept

Trois packages principaux + extras optionnels + catalogue communautaire OptunaHub.

### API / commandes

```bash
# Core (Python ≥ 3.9)
pip install optuna

# Visualisation Plotly (recommandé)
pip install plotly nbformat

# Backend CMA-ES (souvent déjà tiré en dépendance ; sinon)
pip install cmaes

# Dashboard (Python ≥ 3.10)
pip install optuna-dashboard

# Intégrations ML / BoTorch (extras au besoin)
pip install "optuna-integration[lightgbm,xgboost,sklearn,botorch,mlflow]"

# Catalogue communautaire (AutoSampler, etc.)
pip install optunahub
```

| Package | Contenu |
|---|---|
| `optuna` | Core : Study, samplers, pruners, storages, viz, importance, artifacts |
| `plotly` / `matplotlib` | Backends de `optuna.visualization` / `.matplotlib` |
| `cmaes` | Backend de `CmaEsSampler` |
| `optuna-dashboard` | UI web, HITL forms, preferential, notes, LLM helpers |
| `optuna-integration` | Callbacks LightGBM/XGBoost/…, `BoTorchSampler`, DaskStorage, tracking |
| `optunahub` | `optunahub.load_module` — ex. AutoSampler |

### Quand l’utiliser en quant

- Stack minimal : `optuna` + `plotly` + SQLite pour proto.
- Overnight / équipe : `optuna-dashboard` + Postgres.
- BO / deep vol : extras `botorch` (+ torch).
- Choix auto de sampler : `optunahub` AutoSampler.

### Pièges

- Dashboard exige Python ≥ 3.10.
- `BoTorchSampler` vit dans **integration**, pas le core (le core a `GPSampler`).
- Ne pas confondre `pip install optuna[…]` legacy avec le split `optuna-integration`.

### Exemple — vérif d’environnement

```python
import optuna

print("optuna", optuna.__version__)
print("samplers:", [x for x in dir(optuna.samplers) if x.endswith("Sampler")])
print("pruners:", [x for x in dir(optuna.pruners) if x.endswith("Pruner")])
```

---

## 3. Anatomie Study / Trial / Objective

### Concept

| Concept | Rôle |
|---|---|
| **Study** | Une campagne d’optimisation (un problème) |
| **Trial** | Une évaluation en cours (`suggest_*`, `report`, `set_constraint`) |
| **FrozenTrial** | Snapshot immuable d’un trial terminé |
| **FixedTrial** | Rejouer des params figés (tests / live vs research) |
| **Objective** | Callable `(trial) -> float | Sequence[float]` |
| **direction(s)** | `"minimize"` / `"maximize"` ou liste pour multi-obj |

### API précis

```python
import optuna
from optuna.trial import TrialState

study = optuna.create_study(
    study_name="fx_mr_eurusd",
    storage=None,                      # InMemory ; ou URL RDB
    direction="maximize",              # XOR directions=[...]
    sampler=optuna.samplers.TPESampler(seed=42),
    pruner=optuna.pruners.MedianPruner(),
    load_if_exists=False,
)

def objective(trial: optuna.Trial) -> float:
    x = trial.suggest_float("x", -10, 10)
    trial.set_user_attr("symbol", "EURUSD")
    return -(x - 2.0) ** 2

study.optimize(objective, n_trials=50, timeout=None, n_jobs=1, show_progress_bar=True)

print(study.best_params, study.best_value, study.best_trial.number)
df = study.trials_dataframe()
```

États : `RUNNING`, `WAITING`, `COMPLETE`, `PRUNED`, `FAIL`.

Helpers Study :

| API | Usage |
|---|---|
| `optuna.load_study(study_name, storage)` | Reprendre |
| `optuna.delete_study(...)` | Supprimer |
| `optuna.copy_study(...)` | Migrer entre storages |
| `optuna.get_all_study_names(storage)` | Lister |
| `optuna.get_all_study_summaries(storage)` | Résumés |
| `study.stop()` | Arrêt depuis objective/callback |
| `study.set_metric_names([...])` | Nommer les axes multi-obj |
| `optuna.study.MaxTrialsCallback(n)` | Stop après N trials COMPLETE (multi-workers) |

### Quand l’utiliser en quant

- Une Study = une campagne (ex. calib Heston EURUSD 1h, ou HPO mean-reversion).
- `user_attrs` : symbole, timeframe, seed, régime, coûts, version du backtester.
- `load_if_exists=True` pour overnight.

### Pièges

- Ne **pas** combiner `direction` et `directions`.
- Constructeur `Study(...)` direct déconseillé → préférer `create_study` / `load_study`.
- `best_*` mono-objectif ; multi → `best_trials` (Pareto).

### Exemple — objective avec user_attrs et stop

```python
import optuna

def objective(trial: optuna.Trial) -> float:
    lookback = trial.suggest_int("lookback", 10, 100)
    trial.set_user_attr("tf", "H1")
    trial.set_user_attr("pair", "EURUSD")
    score = -abs(lookback - 37)  # proxy
    if trial.number >= 20:
        trial.study.stop()
    return score

study = optuna.create_study(direction="maximize", study_name="demo_stop")
study.optimize(objective, n_trials=100)
print("stopped at", len(study.trials), "best", study.best_params)
```

---

## 4. API ask/tell, enqueue_trial, frozen trials

### Concept

`optimize(objective)` encapsule la boucle. **Ask-and-Tell** expose `ask()` / `tell()` pour boucles custom, batch, pruning manuel, intégration dans un code existant sans refactor en `def objective(trial)`.

Warm-start :

- `enqueue_trial(params)` → trial **à évaluer** (WAITING).
- `add_trial(create_trial(...))` → trial **déjà évalué** (historique).

### API précis

```python
import optuna
from optuna.trial import create_trial, TrialState
from optuna.distributions import FloatDistribution

study = optuna.create_study(direction="maximize")

# --- Warm-start : params déjà connus (baseline live) ---
study.enqueue_trial({"lookback": 20, "z_entry": 2.0})

# --- Warm-start : résultat déjà calculé ---
study.add_trial(
    create_trial(
        params={"lookback": 30, "z_entry": 1.5},
        distributions={
            "lookback": FloatDistribution(5, 100),  # IntDistribution en prod
            "z_entry": FloatDistribution(0.5, 3.0),
        },
        value=1.12,
        state=TrialState.COMPLETE,
    )
)

# --- Ask / Tell define-by-run ---
for _ in range(5):
    trial = study.ask()
    lookback = trial.suggest_int("lookback", 5, 100)
    z_entry = trial.suggest_float("z_entry", 0.5, 3.0)
    sharpe = 1.0 / (1.0 + abs(lookback - 25) + abs(z_entry - 2.0))  # proxy
    study.tell(trial, sharpe)

# --- Define-and-run : distributions passées à ask ---
dists = {
    "lookback": optuna.distributions.IntDistribution(5, 100),
    "z_entry": optuna.distributions.FloatDistribution(0.5, 3.0),
}
trial = study.ask(dists)
sharpe = 0.9
study.tell(trial, sharpe)  # params déjà dans trial.params

print(study.best_params, study.best_value)
```

Pruning manuel avec ask/tell :

```python
study = optuna.create_study(
    direction="maximize",
    pruner=optuna.pruners.MedianPruner(n_startup_trials=3, n_warmup_steps=1),
)

for _ in range(10):
    trial = study.ask()
    lr = trial.suggest_float("lr", 1e-4, 1e-1, log=True)
    pruned = False
    score = 0.0
    for step in range(5):
        score = 1.0 - abs(lr - 0.01) * (step + 1) * 0.1
        trial.report(score, step)
        if trial.should_prune():
            pruned = True
            break
    if pruned:
        study.tell(trial, state=TrialState.PRUNED)
    else:
        study.tell(trial, score)
```

`FrozenTrial` : `study.best_trial`, éléments de `study.trials`, argument des callbacks `(study, frozen_trial)`.

`FixedTrial` : rejouer une config sans Study.

```python
from optuna.trial import FixedTrial

def evaluate(trial):
    return trial.suggest_float("x", 0, 1) ** 2

print(evaluate(FixedTrial({"x": 0.3})))
```

### Quand l’utiliser en quant

- Intégrer Optuna dans un orchestrateur de backtests existant.
- Batch de configs évaluées en vectorisé / sur GPU.
- Forcer la baseline live via `enqueue_trial`.
- Rejouer params paper/live avec `FixedTrial`.

### Pièges

- Oublier `tell` → trials zombies RUNNING.
- `tell(trial, state=PRUNED)` sans value vs `tell(trial, value)`.
- Distributions incohérentes entre `add_trial` et l’espace réel.
- Avec `n_jobs≠1`, reproductibilité dégradée (reseed).

---

## 5. Espace de recherche (search space)

### Concept

**Define-by-run** : les `suggest_*` sont appelés dynamiquement dans l’objective (branches Python = espace conditionnel naturel).  
**Define-and-run** : distributions pré-déclarées (`ask(fixed_distributions)`, `GridSampler`).

### API précis

| Méthode | Type | Options clés | Remplace (déprécié → retrait v6) |
|---|---|---|---|
| `suggest_float(name, low, high, *, step=None, log=False)` | float | `log=True` **sans** `step` | `suggest_uniform`, `suggest_loguniform`, `suggest_discrete_uniform` |
| `suggest_int(name, low, high, *, step=1, log=False)` | int | `log=True` implique `step=1` | — |
| `suggest_categorical(name, choices)` | cat | str/int/float/bool/None | — |

Distributions actuelles à préférer (`optuna.distributions`) : `FloatDistribution`, `IntDistribution`, `CategoricalDistribution`.  
Helpers CLI / sérialisation : `distribution_to_json`, `json_to_distribution`, `check_distribution_compatibility`.  
Anciennes alias (`UniformDistribution`, `LogUniformDistribution`, `DiscreteUniformDistribution`, `IntUniformDistribution`, `IntLogUniformDistribution`) : encore listées dans l’API de référence — **préférer** `FloatDistribution` / `IntDistribution` avec `log=` / `step=` (aligné avec `suggest_float` / `suggest_int`).  
Intersection pour sampling relatif : `optuna.search_space.IntersectionSearchSpace` (pas un sampler ; déplacé hors `optuna.samplers` en v4).

### Quand l’utiliser en quant

| Param | Suggest typique |
|---|---|
| κ, σ, lr, seuil, risk_pct | `suggest_float(..., log=True)` |
| lookback, lags, horizon | `suggest_int` |
| TF, session, type signal | `suggest_categorical` |
| Si modèle = GARCH_t → df | branche define-by-run |

### Pièges

- `log=True` + `step` interdit sur float.
- Dimensionnalité : difficulté ~exponentielle → figer les knobs secondaires (`PartialFixedSampler` / enqueue).
- Ordre/nom des `suggest_*` stables entre trials pour le relative sampling.


### IntersectionSearchSpace & sampling relatif

Les samplers « relatifs » (`sample_relative`, ex. CMA-ES, GP, TPE multivariate) s’appuient sur l’**intersection** des espaces vus dans les trials terminés.

```python
import optuna
from optuna.search_space import IntersectionSearchSpace, intersection_search_space

study = optuna.create_study(direction="minimize")

def objective(trial):
    model = trial.suggest_categorical("model", ["linear", "tree"])
    C = trial.suggest_float("C", 1e-3, 10.0, log=True)
    if model == "tree":
        depth = trial.suggest_int("depth", 2, 8)  # absent des trials "linear"
    return C  # proxy

study.optimize(objective, n_trials=20)

# API documentée (optuna.search_space)
iss = IntersectionSearchSpace(include_pruned=False)
space = iss.calculate(study)  # params présents dans *tous* les trials considérés
# équivalent fonctionnel :
space2 = intersection_search_space(study.trials)
print(sorted(space.keys()))
```

| Règle | Pourquoi |
|---|---|
| **Mêmes noms** `suggest_*` d’un trial à l’autre pour un même param | Sinon le relative sampling ignore / resample indépendamment |
| Distributions **cohérentes** (mêmes bornes / choices) | Changer `low/high` entre trials casse l’intersection |
| Espace conditionnel | OK en define-by-run ; pour TPE, `multivariate=True, group=True` aide (doc TPESampler) |
| `include_pruned=True` | Inclure les trials PRUNED dans le calcul d’intersection (défaut `False`) |

**Pièges espace dynamique :**

1. Renommer un param (`lr` → `learning_rate`) = nouveau knobs pour Optuna.
2. Appeler `suggest_float("x", 0, 1)` puis plus tard `suggest_float("x", -1, 1)` → conflit de distribution.
3. Branches rares → peu de trials dans une sous-espace → sampler relatif peu informé (compléter avec Random / plus de `n_startup_trials`).
4. `GridSampler` / define-and-run : la grille doit **matcher** les `suggest_*` (voir §6).

### Exemple — espace conditionnel FX

```python
import optuna

def objective(trial: optuna.Trial) -> float:
    signal = trial.suggest_categorical("signal", ["zscore", "breakout"])
    lookback = trial.suggest_int("lookback", 10, 120)

    if signal == "zscore":
        z_entry = trial.suggest_float("z_entry", 1.0, 3.0)
        z_exit = trial.suggest_float("z_exit", 0.0, 1.0)
        score = -abs(lookback - 40) - abs(z_entry - 2.0) - abs(z_exit - 0.5)
    else:
        atr_mult = trial.suggest_float("atr_mult", 0.5, 3.0, step=0.1)
        session = trial.suggest_categorical("session", ["LDN", "NY", "ASIA"])
        bonus = {"LDN": 0.2, "NY": 0.1, "ASIA": 0.0}[session]
        score = -abs(lookback - 55) - abs(atr_mult - 1.5) + bonus

    # stop commun
    stop_atr = trial.suggest_float("stop_atr", 0.5, 4.0)
    score -= abs(stop_atr - 2.0) * 0.1
    return score

study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=0, multivariate=True),
)
study.optimize(objective, n_trials=40)
print(study.best_params)
```

---

## 6. Samplers — inventaire complet

### Concept

Le **Sampler** propose les paramètres du prochain trial. Défaut core : **`TPESampler`**.  
Pour ne pas choisir : **AutoSampler** via OptunaHub (`optunahub`), hors core.

### Tableau inventaire (doc `optuna.samplers`)

| Sampler | Rôle | Forces / limites | Budget typique | Quant |
|---|---|---|---|---|
| `TPESampler` | TPE (défaut ; multi-obj défaut v5) | float/int/cat, conditional, multi-obj, contraintes, pruning | 100–1000 | Défaut HPO stratégies FX |
| `CmaEsSampler` | CMA-ES (`cmaes`) | Continu fort ; cat inefficient ; **pas** multi-obj | 1k–10k | Calib continue dense (Heston, filtres) |
| `GPSampler` | GP Bayesian (core, stabilisé v5) | Sample-efficient ; multi-obj+contraintes | ≤ ~500 | Objectifs chers, peu d’essais |
| `NSGAIISampler` | NSGA-II | Pareto + contraintes ; pruning × en multi | 100–10k | Return vs risk (2–3 obj.) |
| `NSGAIIISampler` | NSGA-III | Mieux si nombreux objectifs | 100–10k | ≥3–4 métriques |
| `RandomSampler` | Aléatoire | Baseline ; pruning OK | libre | Baseline anti-biais |
| `QMCSampler` | Quasi-MC | Couverture ; cat moins efficient | libre | DOE initial space-filling |
| `GridSampler` | Grille define-and-run | Exhaustif sur grille fournie | #combinaisons | Petites grilles discrètes |
| `BruteForceSampler` | Exhaustif define-by-run | Explore l’espace dynamique | #combinaisons | Enums / espaces minuscules |
| `PartialFixedSampler` | Params partiellement fixés | Wrappe `base_sampler` | = base | Geler knobs live |
| `BoTorchSampler` | BO BoTorch (**integration**) | Budget serré ; deps torch/botorch | 10–100 | BO avancée / stack PyTorch |
| AutoSampler (Hub) | `optunahub` `auto_sampler` | Sélection auto | — | Quand on ne veut pas choisir |

> Pas de sampler nommé `RelativeSampler` / `IntersectionSampler` dans le core actuel. Le sampling relatif passe par `sample_relative` ; `IntersectionSearchSpace` est dans `optuna.search_space`.


### Défauts & changements v5.0 (release officielle)

| Changement | Détail vérifié (tag `v5.0.0`) |
|---|---|
| `TPESampler` défaut mono-obj | `multivariate` auto/`True` pour single-obj ; `constant_liar=True` par défaut |
| Multi-objectif | **`TPESampler` remplace `NSGAIISampler`** comme sampler par défaut |
| `GPSampler` | API **stable** ; acquisitions q-batch Monte Carlo (qLogEI / qLogCEI / qLogEHVI / qLogCEHVI) |
| Contraintes | `trial.set_constraint` + propriété `constraints` ; `constraints_func` **déprécié** (retrait prévu v7) |
| Importance | `PedAnovaImportanceEvaluator` = défaut |
| Timestamps storage | UTC (plus local) dans RDB/Journal — backup avant upgrade |
| Retraits | module `optuna.multi_objective` ; intégrations AllenNLP/Chainer/MXNet ; `axis_order` Pareto ; `system_attrs` Study/Trial |

Signature utile `TPESampler` (params non dépréciés à privilégier) :

```python
optuna.samplers.TPESampler(
    n_startup_trials=10,
    n_ei_candidates=24,
    seed=None,
    multivariate=None,   # None → True si 1 obj., False si multi-obj.
    group=False,         # + multivariate=True → espaces conditionnels groupés
    constant_liar=True,  # défaut v5 — batch / distributed / zombies RUNNING
    # constraints_func=...  # DÉPRÉCIÉ → trial.set_constraint
)
```

**Reproductibilité :** passer `seed` au sampler. Avec `n_jobs≠1` ou optimisation distribuée, Optuna **reseede** → audits quant : **1 worker + seed fixe**.

### Exemples clés

#### TPESampler (défaut)

```python
import optuna

sampler = optuna.samplers.TPESampler(
    seed=42,
    n_startup_trials=10,
    multivariate=True,   # défaut True depuis v5
    constant_liar=True,  # défaut True depuis v5 — utile batch/distrib
)
study = optuna.create_study(direction="maximize", sampler=sampler)

def objective(trial):
    w = trial.suggest_int("window", 10, 80)
    thr = trial.suggest_float("thr", 0.5, 3.0)
    return -((w - 30) / 30) ** 2 - (thr - 1.8) ** 2

study.optimize(objective, n_trials=60)
```

#### CmaEsSampler — calib continue

```python
import optuna

sampler = optuna.samplers.CmaEsSampler(seed=0, n_startup_trials=5)
study = optuna.create_study(direction="minimize", sampler=sampler)

def objective(trial):
    # proxy calib OU : minimiser erreur
    theta = trial.suggest_float("theta", 0.01, 5.0, log=True)
    mu = trial.suggest_float("mu", -0.01, 0.01)
    sigma = trial.suggest_float("sigma", 1e-4, 0.05, log=True)
    # cible fictive
    return (theta - 0.5) ** 2 + (mu - 0.0) ** 2 + (sigma - 0.01) ** 2

study.optimize(objective, n_trials=80)
print(study.best_params)
```

#### GPSampler — peu d’essais chers

```python
import optuna

study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.GPSampler(seed=1),
)

def objective(trial):
    risk = trial.suggest_float("risk_pct", 0.001, 0.02, log=True)
    return -abs(risk - 0.005)  # proxy

study.optimize(objective, n_trials=25)
```

#### NSGA-II — Pareto

```python
import optuna

study = optuna.create_study(
    directions=["maximize", "minimize"],
    sampler=optuna.samplers.NSGAIISampler(seed=0, population_size=32),
)
study.set_metric_names(["sharpe", "maxdd"])

def objective(trial):
    lookback = trial.suggest_int("lookback", 10, 100)
    z = trial.suggest_float("z", 1.0, 3.0)
    sharpe = 1.5 - abs(lookback - 40) / 80 - abs(z - 2) / 4
    maxdd = 0.05 + abs(lookback - 50) / 500 + abs(z - 1.5) / 20
    return sharpe, maxdd

study.optimize(objective, n_trials=80)
print("Pareto size:", len(study.best_trials))
```

#### NSGAIIISampler — nombreux objectifs

```python
import optuna

# Préférer NSGA-III quand le nombre d'objectifs augmente (doc samplers : budgets 100–10000)
study = optuna.create_study(
    directions=["maximize", "minimize", "minimize"],
    sampler=optuna.samplers.NSGAIIISampler(seed=0),
)
study.set_metric_names(["reward", "latency_ms", "memory_mb"])

def objective(trial):
    w = trial.suggest_float("w", 0.1, 2.0)
    return (w, abs(w - 1.0) * 10, w * 50)  # proxy 3 objectifs

study.optimize(objective, n_trials=40)
print(len(study.best_trials))
```

#### GridSampler / BruteForceSampler / QMC / Random / PartialFixed

```python
import optuna

# Grid (define-and-run)
search_space = {
    "lookback": [10, 20, 40, 60],
    "z": [1.5, 2.0, 2.5],
}
study_grid = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.GridSampler(search_space),
)

def obj_grid(trial):
    # les valeurs viennent de la grille
    lb = trial.suggest_int("lookback", 10, 60)  # doit matcher la grille
    z = trial.suggest_float("z", 1.5, 2.5)
    return -abs(lb - 20) - abs(z - 2.0)

study_grid.optimize(obj_grid)  # n_trials = produit des grilles

# QMC + Random baseline
optuna.create_study(sampler=optuna.samplers.QMCSampler(seed=0))
optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=0))

# PartialFixed — geler le stop live, optimiser le reste
base = optuna.samplers.TPESampler(seed=0)
fixed = optuna.samplers.PartialFixedSampler(
    fixed_params={"stop_atr": 2.0},
    base_sampler=base,
)
study_pf = optuna.create_study(direction="maximize", sampler=fixed)

def obj_pf(trial):
    lookback = trial.suggest_int("lookback", 10, 80)
    stop_atr = trial.suggest_float("stop_atr", 0.5, 4.0)  # sera forcé à 2.0
    return -abs(lookback - 35) - abs(stop_atr - 2.0)

study_pf.optimize(obj_pf, n_trials=20)
print(study_pf.best_params)  # stop_atr == 2.0
```

#### AutoSampler (OptunaHub)

```python
# pip install optunahub
import optuna
import optunahub

mod = optunahub.load_module(package="samplers/auto_sampler")
sampler = mod.AutoSampler(seed=42)
study = optuna.create_study(direction="maximize", sampler=sampler)
# puis optimize comme d'habitude
```

#### BoTorchSampler (integration)

```python
# pip install "optuna-integration[botorch]"
from optuna_integration import BoTorchSampler
import optuna

study = optuna.create_study(
    direction="maximize",
    sampler=BoTorchSampler(seed=0),
)
```

---

## 7. Pruners — inventaire + backtest / early-stop

### Concept

Un **Pruner** décide, via `trial.report(value, step)` + `trial.should_prune()`, si un trial mono-objectif doit s’arrêter tôt.  
**Warning officiel :** module `pruners` attendu **uniquement** pour single-objective.

### Tableau inventaire

| Pruner | Mécanisme | Usage backtest cher |
|---|---|---|
| `MedianPruner` | Pire que médiane au même step (défaut) | Coupe rapide des runs médiocres |
| `PercentilePruner` | Garde un percentile | Agressivité réglable |
| `SuccessiveHalvingPruner` | ASHA — budget aux prometteurs | Folds WF progressifs |
| `HyperbandPruner` | Hyperband (brackets SHA) | Souvent bon avec TPE |
| `ThresholdPruner` | Seuils hors bornes | Abort si DD/PnL franchit une limite |
| `PatientPruner` | Wrap + tolérance | Évite coupe trop tôt (bruit) |
| `WilcoxonPruner` | Wilcoxon signed-rank | Comparaison statistique de courbes |
| `NopPruner` | Ne prune jamais | Calib qui doit aller au bout |

Reco benchmarks Optuna (non-DL) : Random+Median ; TPE+Hyperband.

### API précis

```python
trial.report(intermediate_value, step)
if trial.should_prune():
    raise optuna.TrialPruned()
```

Ask/tell : `study.tell(trial, state=TrialState.PRUNED)`.

### Quand l’utiliser en quant

- `step` = index de fenêtre walk-forward (ordonné dans le temps).
- ThresholdPruner si MaxDD intermédiaire > limite hard.
- NopPruner pour calib MLE-like qui doit finir.

### Pièges

- Pruner ≠ validation OOS.
- Métrique intermédiaire non monotone → faux prunes.
- Ne pas utiliser en multi-objectif.

### Exemple — faux backtest FX avec pruning mid-run

```python
import numpy as np
import optuna

rng = np.random.default_rng(0)
# série log-prix synthétique
n = 2000
rets = rng.normal(0, 0.0007, size=n)
logp = np.cumsum(rets)

def fake_fx_backtest(lookback: int, z_entry: float, stop: float, end_idx: int) -> float:
    """Sharpe proxy sur [0, end_idx) — mean-reversion z-score simplifié."""
    window = logp[:end_idx]
    if end_idx < lookback + 10:
        return -1.0
    ma = np.convolve(window, np.ones(lookback) / lookback, mode="valid")
    # alignement naïf
    seg = window[-len(ma) :]
    std = np.std(seg - ma) + 1e-12
    z = (seg - ma) / std
    pos = np.where(z > z_entry, -1.0, np.where(z < -z_entry, 1.0, 0.0))
    pnl = pos[:-1] * np.diff(seg)
    # stop grossier
    if np.min(np.cumsum(pnl)) < -stop:
        pnl = pnl * 0.5
    if pnl.std() < 1e-12:
        return -1.0
    return float(pnl.mean() / pnl.std() * np.sqrt(252 * 24))  # H1-ish

def objective(trial: optuna.Trial) -> float:
    lookback = trial.suggest_int("lookback", 10, 100)
    z_entry = trial.suggest_float("z_entry", 1.0, 3.0)
    stop = trial.suggest_float("stop", 0.01, 0.05)

    # walk-forward steps : 40%, 60%, 80%, 100% de l'historique
    fracs = [0.4, 0.6, 0.8, 1.0]
    score = -1.0
    for step, frac in enumerate(fracs):
        end_idx = int(n * frac)
        score = fake_fx_backtest(lookback, z_entry, stop, end_idx)
        trial.report(score, step)
        if trial.should_prune():
            raise optuna.TrialPruned()
    trial.set_user_attr("pair", "EURUSD")
    return score

study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=0),
    pruner=optuna.pruners.HyperbandPruner(
        min_resource=1, max_resource=4, reduction_factor=3
    ),
)
study.optimize(objective, n_trials=40)
print("best", study.best_params, "value", study.best_value)
print("pruned", sum(t.state == optuna.trial.TrialState.PRUNED for t in study.trials))
```

Patient + Threshold :

```python
pruner = optuna.pruners.PatientPruner(
    optuna.pruners.MedianPruner(n_warmup_steps=1),
    patience=2,
)
# ou
thr = optuna.pruners.ThresholdPruner(lower=-0.5)  # prune si report < -0.5
```

---

## 8. Multi-objectif, contraintes & Pareto

### Concept

- `directions=["maximize", "minimize", ...]` ; l’objective retourne un **tuple**.
- Front de Pareto : `study.best_trials` (pas `best_trial`).
- **Contraintes (v5+)** : `trial.set_constraint(key, value)` — faisable si **toutes** les valeurs ≤ 0.
- `constraints_func` sur les samplers : **déprécié v5**, retrait prévu v7 → migrer vers `set_constraint`.
- Samplers avec contraintes : NSGA-II/III, TPE, GP (+ BoTorch, Auto). **CmaEsSampler : pas multi-obj.**

### API précis

```python
import optuna

def objective(trial: optuna.Trial):
    lookback = trial.suggest_int("lookback", 10, 100)
    leverage = trial.suggest_float("leverage", 1.0, 20.0)
    sharpe = 1.2 - abs(lookback - 45) / 100
    maxdd = 0.08 + leverage / 200

    # contrainte : leverage <= 10  →  value = leverage - 10 <= 0
    trial.set_constraint("leverage_cap", leverage - 10.0)
    # contrainte : min trades fictif
    n_trades = 200 - lookback
    trial.set_constraint("min_trades", 50 - n_trades)  # <=0 si n_trades >= 50

    return sharpe, maxdd

study = optuna.create_study(
    directions=["maximize", "minimize"],
    sampler=optuna.samplers.NSGAIISampler(seed=0),
)
study.set_metric_names(["sharpe", "maxdd"])
study.optimize(objective, n_trials=60)

print("Pareto:", len(study.best_trials))
best_sharpe = max(study.best_trials, key=lambda t: t.values[0])
print(best_sharpe.params, best_sharpe.values)
# v5+ : dict[str, float] — faisable ssi toutes les valeurs ≤ 0
print("constraints", best_sharpe.constraints)
print("feasible", all(v <= 0 for v in best_sharpe.constraints.values()))
```

Visualisation :

```python
fig = optuna.visualization.plot_pareto_front(study, target_names=["sharpe", "maxdd"])
# fig.show()
```

### Quand l’utiliser en quant

- Maximize Sharpe + minimize MaxDD / turnover / CVaR.
- Contraintes hard : levier, marge, nb min de trades, Feller (Heston).
- Si trop d’objectifs → curse of dimensionality sur le front : passer certains en contraintes.

### Pièges

- Utiliser `best_trial` en multi → erreur.
- `constraints_func` legacy : préférer `set_constraint`.
- Pruners incompatibles multi-obj.
- Convention contrainte : **≤ 0 = faisable** (pas ≥ 0).

---

## 9. Importance des hyperparamètres

### Concept

`optuna.importance.get_param_importances(study, evaluator=...)` estime l’importance relative des params sur les trials COMPLETE.

| Evaluator | Notes |
|---|---|
| `PedAnovaImportanceEvaluator` | **Défaut depuis v5** (PED-ANOVA) |
| `FanovaImportanceEvaluator` | fANOVA classique |
| `MeanDecreaseImpurityImportanceEvaluator` | MDI (forêt) |
| SHAP | Via `optuna-integration` (si installé) |

### API précis

```python
import optuna
from optuna.importance import (
    get_param_importances,
    PedAnovaImportanceEvaluator,
    FanovaImportanceEvaluator,
    MeanDecreaseImpurityImportanceEvaluator,
)

# après une study mono-objectif suffisamment peuplée
imp = get_param_importances(study)  # PedAnova par défaut
print(imp)

imp_f = get_param_importances(study, evaluator=FanovaImportanceEvaluator())
imp_m = get_param_importances(study, evaluator=MeanDecreaseImpurityImportanceEvaluator())

# multi-obj : cibler un objectif
imp0 = get_param_importances(study, target=lambda t: t.values[0])

optuna.visualization.plot_param_importances(study)
```

### Quand l’utiliser en quant

- Réduire l’espace : figer les params à faible importance.
- Expliquer quels knobs drivert Sharpe vs DD (target lambda).

### Pièges

- PedAnova vise des **faibles** valeurs de `target` (warning doc si maximize) → pour maximize, `target=lambda t: -t.value`.
- Peu de trials → importances instables.
- Params conditionnels absents de certains trials → interprétation prudente.

---

## 10. Storage & parallélisme

### Concept

Sans storage : `InMemoryStorage` (défaut) — perdu à la fin du process.  
Avec storage partagé : plusieurs workers, Dashboard, reprise overnight.

### Tableau backends

| Backend | Classe / URL | Cas d’usage |
|---|---|---|
| InMemory | `storage=None` | Proto local |
| RDB SQLite | `sqlite:///study.db` | Overnight mono-machine |
| RDB Postgres | `postgresql+psycopg2://user:pass@host/db` | Multi-node, prod |
| RDB MySQL | `mysql+pymysql://...` | Idem |
| Journal file | `JournalStorage(JournalFileBackend(path))` | Multi-process même machine |
| Journal Redis | `JournalStorage(JournalRedisBackend(url))` | Journal Redis ; cluster = atomicité non garantie |
| gRPC proxy | `GrpcStorageProxy` + `run_grpc_proxy_server` | Très nombreux workers devant RDB |
| Dask | `DaskStorage` (`optuna-integration`) | Clusters Dask |

Legacy : `JournalRedisStorage` déprécié v4 → retrait v6 : utiliser `journal.JournalRedisBackend`.

### API précis — SQLite overnight + workers

```python
import optuna
from optuna.storages import JournalStorage
from optuna.storages.journal import JournalFileBackend

STORAGE = "sqlite:///fx_optuna.db"
NAME = "eurusd_mr_h1"

study = optuna.create_study(
    study_name=NAME,
    storage=STORAGE,
    direction="maximize",
    load_if_exists=True,
    sampler=optuna.samplers.TPESampler(seed=None),  # seeds distincts multi-process
)
# study.optimize(objective, n_trials=100)

# Journal file
storage_j = JournalStorage(JournalFileBackend("./journal.log"))
study_j = optuna.create_study(storage=storage_j, study_name="j1", load_if_exists=True)

# Heartbeats / stale trials / gRPC → voir section 22 (recettes complètes)
# from optuna.storages import fail_stale_trials, RetryHeartbeatStaleTrialCallback
```

Parallélisme :

1. Plusieurs process Python, **même** `storage` + `study_name` + `load_if_exists=True`.
2. `n_jobs` dans `optimize` = threads (GIL) — OK I/O ; CPU-bound → process + RDB/Journal.
3. `MaxTrialsCallback` pour plafond global multi-workers.

```python
study.optimize(
    objective,
    n_trials=1000,
    callbacks=[optuna.study.MaxTrialsCallback(200, states=(optuna.trial.TrialState.COMPLETE,))],
)
```

### Quand l’utiliser en quant

- Campagne overnight sur plusieurs cœurs / machines.
- Dashboard pointant sur le même SQLite/Postgres.
- Journal file pour multi-process sans serveur SQL.

### Pièges

- SQLite : attention aux writers concurrent très nombreux → Postgres.
- Pickle/joblib d’une Study InMemory : pas portable entre versions Optuna.
- Redis journal en cluster : atomicité non garantie (doc).
- Reproductibilité vs multi-workers : trade-off.

---

## 11. Callbacks, logging, artefacts, terminator, HITL

### Concept

- **Callbacks** : `optimize(..., callbacks=[fn(study, frozen_trial)])`.
- **Artefacts** : fichiers liés à un trial/study (equity curve, YAML, notebook).
- **Terminator** : module core `optuna.terminator.*` — **déprécié**, retrait prévu **v6** → migrer vers OptunaHub `callbacks/terminator` (et visualisations associées sur le Hub).
- **HITL** : formulaires / preferential via **Optuna Dashboard** (pas le core seul).

### Callbacks & logging

```python
import logging
import optuna

# Niveaux : CRITICAL/FATAL, ERROR, WARNING/WARN, INFO, DEBUG
optuna.logging.set_verbosity(optuna.logging.INFO)
# optuna.logging.disable_default_handler() / enable_default_handler()
# optuna.logging.disable_propagation() / enable_propagation()
# niveau courant : optuna.logging.get_verbosity()

def on_trial(study: optuna.Study, trial: optuna.trial.FrozenTrial):
    if trial.state == optuna.trial.TrialState.COMPLETE:
        print(f"#{trial.number} value={trial.value} params={trial.params}")

study = optuna.create_study(direction="maximize")
study.optimize(lambda t: t.suggest_float("x", 0, 1), n_trials=5, callbacks=[on_trial])
```

### Artefacts

```python
import json
import tempfile
from pathlib import Path

import optuna
from optuna.artifacts import FileSystemArtifactStore, upload_artifact, download_artifact

# store local
artifact_path = Path("./artifacts")
artifact_path.mkdir(exist_ok=True)
store = FileSystemArtifactStore(base_path=str(artifact_path))

def objective(trial: optuna.Trial) -> float:
    x = trial.suggest_float("x", -1, 1)
    # écrire un "equity" proxy
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"x": x, "equity": [0, x, 2 * x]}, f)
        tmp = f.name
    # upload lié au trial (API artefacts Optuna — kwargs keyword-only)
    upload_artifact(
        artifact_store=store,
        file_path=tmp,
        study_or_trial=trial,
    )
    return -x ** 2

study = optuna.create_study(direction="maximize")
study.optimize(objective, n_trials=3)
```

Autres stores / helpers documentés (`optuna.artifacts`) :

| API | Rôle |
|---|---|
| `FileSystemArtifactStore(base_path)` | FS local / NFS |
| `Boto3ArtifactStore(bucket_name, client=None, *, avoid_buf_copy=False)` | S3-compatible |
| `GCSArtifactStore(bucket_name, client=None)` | Google Cloud Storage (expérimental depuis v3.4) |
| `Backoff(backend, *, max_retries=10, ...)` | Middleware retry expo |
| `upload_artifact(*, artifact_store, file_path, study_or_trial, ...)` | Upload (kwargs keyword-only) → `artifact_id` |
| `download_artifact(*, artifact_store, file_path, artifact_id)` | Download |
| `get_all_artifact_meta(study_or_trial, *, storage=None)` | Liste `ArtifactMeta` (id, filename, mimetype, encoding) |

```python
# après optimize — récupérer les artefacts du best trial
# Si best_trial est un FrozenTrial et la study utilise un storage RDB :
STORAGE_URL = "sqlite:///fx_optuna.db"  # même URL que create_study
metas = optuna.artifacts.get_all_artifact_meta(
    study.best_trial,
    storage=optuna.storages.get_storage(STORAGE_URL),
)
for meta in metas:
    optuna.artifacts.download_artifact(
        artifact_store=store,
        artifact_id=meta.artifact_id,
        file_path=f"./out/{meta.filename}",
    )
```

Pas d’API officielle de suppression massive d’artefacts — la FAQ Optuna documente un hack ; gérer le lifecycle côté bucket/FS.

### HITL / preferential (Dashboard)

- Widgets : `register_*_form_widgets` (Choice/Slider/TextInput) — côté Dashboard.
- Preferential optimization : `preferential.create_study` / `PreferentialGPSampler` (Dashboard docs).
- Notes Markdown : `save_note` ; graphs custom : `save_plotly_graph_object`.

### Quand l’utiliser en quant

- Callback : log Prometheus / Slack interne, early stop custom.
- Artefacts : equity curves, configs YAML, rapports de fold WF.
- HITL : trader choisit sur le front Pareto / préfère des equity curves.

### Pièges

- Terminator core déprécié — ne pas bâtir dessus.
- Artefacts : configurer le même store pour workers et Dashboard.
- Callbacks trop lourds ralentissent le throughput.

---

## 12. Visualisation (`plot_*`)

### Concept

`optuna.visualization.*` → Plotly.  
`optuna.visualization.matplotlib.*` → Matplotlib.

Deps : `pip install plotly nbformat` (notebooks) ou `matplotlib`.

### Inventaire des plots majeurs

| Fonction | Rôle | Angle quant |
|---|---|---|
| `plot_optimization_history` | Best vs trials | Convergence overnight |
| `plot_intermediate_values` | Courbes `report()` | Folds WF |
| `plot_parallel_coordinate` | Relations multi-params | Clusters de configs |
| `plot_contour` | Surface 2 params | lookback × threshold |
| `plot_slice` | Slice univarié | Sensibilité |
| `plot_param_importances` | Importance HP | Quels knobs driver |
| `plot_pareto_front` | Front multi-obj | Return vs risk |
| `plot_edf` | EDF des objectifs | Distribution |
| `plot_rank` | Ranks | Comparaison relative |
| `plot_timeline` | Timeline trials | Parallélisme / durée |
| `plot_hypervolume_history` | Hypervolume | Qualité Pareto |
| `plot_terminator_improvement` | Terminator | Module déprécié |

### Exemple Plotly

```python
import optuna

def objective(trial):
    a = trial.suggest_float("lookback", 10, 100)
    b = trial.suggest_float("z", 1, 3)
    return -((a - 40) / 40) ** 2 - (b - 2) ** 2

study = optuna.create_study(direction="maximize")
study.optimize(objective, n_trials=40)

fig1 = optuna.visualization.plot_optimization_history(study)
fig2 = optuna.visualization.plot_slice(study, params=["lookback", "z"])
fig3 = optuna.visualization.plot_contour(study, params=["lookback", "z"])
fig4 = optuna.visualization.plot_parallel_coordinate(study)
fig5 = optuna.visualization.plot_param_importances(study)
fig6 = optuna.visualization.plot_timeline(study)
# fig1.show()  # dans un notebook / browser

# Matplotlib
from optuna.visualization.matplotlib import plot_optimization_history as mph
ax = mph(study)
```

Multi-obj :

```python
# study avec directions=[...]
optuna.visualization.plot_pareto_front(study, target_names=["sharpe", "maxdd"])
optuna.visualization.plot_hypervolume_history(study, reference_point=[-1.0, 1.0])
```

### Pièges

- JupyterLab nécessite config Plotly (nbformat).
- `plot_pareto_front` : arg `axis_order` retiré en v5.
- Contour/slice peu informatifs si trop peu de trials.

---

## 13. Optuna Dashboard

### Concept

UI web sur un storage persistant : trials, history, importance, Pareto, notes, artefacts, HITL forms, preferential, helpers LLM.

### API / CLI

```bash
pip install optuna-dashboard

# RDB
optuna-dashboard sqlite:///fx_optuna.db
# → http://localhost:8080

# Config TOML
optuna-dashboard --from-config dashboard.toml

# Redis / journal : voir doc storage URLs Dashboard
```

Python :

```python
from optuna_dashboard import run_server
import optuna

storage = optuna.storages.RDBStorage("sqlite:///fx_optuna.db")
# run_server(storage)  # bloque ; ou WSGI Gunicorn/uWSGI en prod
```

| Feature | Détail |
|---|---|
| CLI | `optuna-dashboard <storage_url>` |
| Docker | `ghcr.io/optuna/optuna-dashboard` |
| Browser Wasm | subset drag&drop SQLite |
| VS Code ext. | Open `.db` / `.sqlite3` |
| Notes Markdown | `save_note` |
| Plotly custom | `save_plotly_graph_object` |
| Artefacts | chemins trial/study |
| HITL forms | Choice/Slider/TextInput |
| Preferential | feedback humain |
| LLM | filtres NL / charts (config) |
| Auth | **pas** first-party → reverse-proxy / SSO |


Tutoriels Dashboard officiels : HITL (`tutorials/hitl.html`), preferential optimization, LLM integration — voir §25.
### Quand l’utiliser

- Superviser une campagne overnight.
- Annoter des trials (régime de marché, incident data).
- Choisir sur le front Pareto avec l’œil du trader (HITL).

### Pièges

- Sans storage persistant partagé avec les workers → Dashboard vide.
- Exposer sans auth sur le réseau = risque.

---

## 14. Intégrations (`optuna-integration`)

### Concept

Callbacks et samplers branchés sur l’écosystème ML. Package séparé : `optuna-integration`.

| Lib | Symboles typiques | Angle quant |
|---|---|---|
| LightGBM | `LightGBMPruningCallback`, `LightGBMTuner` | Meta-labels / ranking |
| XGBoost | `XGBoostPruningCallback` | Boosting |
| CatBoost | `CatBoostPruningCallback` | Boosting |
| scikit-learn | `OptunaSearchCV` | Pipelines / CV |
| PyTorch Lightning / Ignite / Distributed | PruningCallback / Handler / `TorchDistributedTrial` | Deep vol / seq |
| skorch | `SkorchPruningCallback` | Torch sklearn-like |
| TF / Keras | `KerasPruningCallback`, `TFKerasPruningCallback` | DL |
| FastAI | `FastAIPruningCallback` | fastai≥2 |
| BoTorch | `BoTorchSampler` | BO |
| pycma | `PyCmaSampler` | CMA alt. (souvent déprécié*) |
| SHAP | importance Shapley | Explain |
| MLflow* / W&B* / TensorBoard* / Trackio* | Callbacks tracking | Exp tracking |
| Dask | `DaskStorage` | Cluster |

\* Certains tracking/wrappers marqués dépréciés selon README integration — vérifier la version installée.

**Hydra / Ray / joblib** : pas d’intégration first-party listée comme ces callbacks — pattern = workers Optuna + storage partagé, ou envelopper la Study.


### Modules listés par la doc `optuna-integration` (stable)

BoTorch · CatBoost · Comet · Dask · Keras · LightGBM · MLflow · pycma · PyTorch · SHAP · sklearn · skorch · TensorBoard · TensorFlow · Weights & Biases · XGBoost · Trackio.

Retraits confirmés côté v5 / cleanup integration : AllenNLP, Chainer, MXNet (et wrappers associés).

### Exemple — sklearn OptunaSearchCV (esquisse)

```python
# pip install "optuna-integration[sklearn]"
from sklearn.datasets import make_regression
from sklearn.ensemble import RandomForestRegressor
from optuna_integration.sklearn import OptunaSearchCV
import optuna

X, y = make_regression(n_samples=500, n_features=10, random_state=0)
param_distributions = {
    "n_estimators": optuna.distributions.IntDistribution(20, 100),
    "max_depth": optuna.distributions.IntDistribution(2, 16),
}
search = OptunaSearchCV(
    RandomForestRegressor(random_state=0),
    param_distributions,
    n_trials=20,
    random_state=0,
)
search.fit(X, y)
print(search.best_params_)
```

### Pièges

- Importer depuis `optuna.integration` legacy peut être cassé → `optuna_integration`.
- AllenNLP / Chainer / MXNet retirés (v5 / integration cleanup).

---

## 15. Patterns quant finance profonds

Tous les exemples ci-dessous sont des **proxies pédagogiques** (pas un moteur de prod). Remplacez les fonctions de PnL / vraisemblance par votre backtester / pricer.

### 15.1 Mean-reversion FX — fenêtre, z-score, stop

```python
import numpy as np
import optuna

rng = np.random.default_rng(42)
logp = np.cumsum(rng.normal(0, 0.0007, size=3000))

def backtest_mr(lookback, z_entry, z_exit, stop_atr, atr_mult_stop):
    # ATR proxy = rolling std
    ret = np.diff(logp, prepend=logp[0])
    atr = np.array([np.std(ret[max(0, i - lookback): i + 1]) for i in range(len(logp))])
    ma = np.convolve(logp, np.ones(lookback) / lookback, mode="same")
    z = (logp - ma) / (atr + 1e-12)
    pos = 0.0
    pnl = []
    entry = 0.0
    for i in range(lookback, len(logp) - 1):
        if pos == 0:
            if z[i] > z_entry:
                pos = -1.0
                entry = logp[i]
            elif z[i] < -z_entry:
                pos = 1.0
                entry = logp[i]
        else:
            # exit mean reversion ou stop
            if abs(z[i]) < z_exit:
                pos = 0.0
            elif abs(logp[i] - entry) > atr_mult_stop * (atr[i] + 1e-12) * stop_atr:
                pos = 0.0
        pnl.append(pos * (logp[i + 1] - logp[i]))
    pnl = np.asarray(pnl)
    if pnl.std() < 1e-12:
        return -1.0, 1.0
    sharpe = float(pnl.mean() / pnl.std() * np.sqrt(252 * 24))
    maxdd = float(np.max(np.maximum.accumulate(np.cumsum(pnl)) - np.cumsum(pnl)))
    return sharpe, maxdd

def objective(trial: optuna.Trial) -> float:
    lookback = trial.suggest_int("lookback", 10, 120)
    z_entry = trial.suggest_float("z_entry", 1.0, 3.0)
    z_exit = trial.suggest_float("z_exit", 0.0, 1.0)
    stop_atr = trial.suggest_float("stop_atr", 0.5, 3.0)
    atr_mult_stop = trial.suggest_float("atr_mult_stop", 1.0, 5.0)
    sharpe, maxdd = backtest_mr(lookback, z_entry, z_exit, stop_atr, atr_mult_stop)
    trial.set_user_attr("maxdd", maxdd)
    trial.set_constraint("maxdd_cap", maxdd - 0.05)  # MaxDD <= 0.05
    return sharpe

study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=0),
)
study.optimize(objective, n_trials=50)
print(study.best_params, study.best_value, study.best_trial.user_attrs)
```

### 15.2 Multi-obj Sharpe vs MaxDD

```python
import optuna

def objective(trial: optuna.Trial):
    lookback = trial.suggest_int("lookback", 10, 120)
    z_entry = trial.suggest_float("z_entry", 1.0, 3.0)
    sharpe, maxdd = backtest_mr(lookback, z_entry, 0.5, 1.0, 2.0)
    return sharpe, maxdd

study = optuna.create_study(
    directions=["maximize", "minimize"],
    sampler=optuna.samplers.NSGAIISampler(seed=0, population_size=40),
)
study.set_metric_names(["sharpe", "maxdd"])
study.optimize(objective, n_trials=80)

# choix sur le front : ex. meilleur Sharpe sous MaxDD <= 0.03
feasible = [t for t in study.best_trials if t.values[1] <= 0.03]
chosen = max(feasible or study.best_trials, key=lambda t: t.values[0])
print(chosen.params, chosen.values)
```

### 15.3 Calibration Heston / OU / GARCH-like

Optuna minimise une erreur de pricing / moment matching / −loglik (vous fournissez le modèle).

```python
import numpy as np
import optuna

# --- OU : dX = theta (mu - X) dt + sigma dW ---
def simulate_ou(theta, mu, sigma, x0=0.0, n=1000, dt=1 / 252, seed=0):
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = x0
    for t in range(1, n):
        x[t] = x[t - 1] + theta * (mu - x[t - 1]) * dt + sigma * np.sqrt(dt) * rng.normal()
    return x

# "marché" synthétique
true = dict(theta=1.2, mu=0.01, sigma=0.05)
market = simulate_ou(**true, seed=1)

def ou_objective(trial: optuna.Trial) -> float:
    theta = trial.suggest_float("theta", 0.05, 5.0, log=True)
    mu = trial.suggest_float("mu", -0.05, 0.05)
    sigma = trial.suggest_float("sigma", 1e-3, 0.2, log=True)
    sim = simulate_ou(theta, mu, sigma, seed=2)
    # RMSE sur moments + path (proxy)
    err = np.mean((sim - market) ** 2)
    err += (sim.mean() - market.mean()) ** 2 + (sim.std() - market.std()) ** 2
    return float(err)

study_ou = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.CmaEsSampler(seed=0),
)
study_ou.optimize(ou_objective, n_trials=60)
print("OU calib", study_ou.best_params, "true", true)


# --- Heston (params) + contrainte Feller 2*kappa*theta >= xi^2 ---
def heston_objective(trial: optuna.Trial) -> float:
    kappa = trial.suggest_float("kappa", 0.1, 10.0, log=True)
    theta = trial.suggest_float("theta", 0.01, 0.25, log=True)
    xi = trial.suggest_float("xi", 0.05, 1.0, log=True)
    rho = trial.suggest_float("rho", -0.99, -0.1)
    v0 = trial.suggest_float("v0", 0.01, 0.25, log=True)
    # Feller : 2 kappa theta - xi^2 >= 0  → constraint = xi^2 - 2*kappa*theta <= 0
    trial.set_constraint("feller", xi ** 2 - 2 * kappa * theta)
    # erreur proxy vs cibles (vol term structure fictive)
    target = np.array([0.04, 0.045, 0.05])
    model = np.array([v0, theta, theta + xi * 0.01])
    return float(np.mean((model - target) ** 2) + 0.01 * (rho + 0.7) ** 2)

study_h = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.TPESampler(seed=0),
)
study_h.optimize(heston_objective, n_trials=80)
print("Heston", study_h.best_params)


# --- GARCH(1,1)-like define-by-run ---
def garch_objective(trial: optuna.Trial) -> float:
    family = trial.suggest_categorical("family", ["garch", "gjr", "t"])
    omega = trial.suggest_float("omega", 1e-8, 1e-3, log=True)
    alpha = trial.suggest_float("alpha", 1e-4, 0.3, log=True)
    beta = trial.suggest_float("beta", 0.5, 0.99)
    trial.set_constraint("stationarity", alpha + beta - 0.999)  # alpha+beta < 1
    nu = 8.0
    if family == "t":
        nu = trial.suggest_float("nu", 3.0, 30.0)
    if family == "gjr":
        gamma = trial.suggest_float("gamma", 0.0, 0.3)
    else:
        gamma = 0.0
    # -loglik proxy
    return float((alpha + beta - 0.95) ** 2 + (omega - 1e-6) ** 2 + 0.01 * (nu - 8) ** 2 + gamma ** 2)

study_g = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=0))
study_g.optimize(garch_objective, n_trials=50)
print("GARCH-like", study_g.best_params)
```

### 15.4 Walk-forward / purged CV (esquisse)

Optuna **n’a pas** de splitter finance natif — vous codez la CV dans l’objective.

```python
import numpy as np
import optuna

def purged_folds(n, n_folds=5, embargo=20):
    """Folds temporels simples + embargo (esquisse, pas CombinatorialPurgedCV López de Prado)."""
    fold_sizes = np.full(n_folds, n // n_folds)
    fold_sizes[: n % n_folds] += 1
    indices = np.arange(n)
    current = 0
    folds = []
    for fs in fold_sizes:
        start, stop = current, current + fs
        test = indices[start:stop]
        train = np.concatenate([indices[: max(0, start - embargo)], indices[stop + embargo :]])
        folds.append((train, test))
        current = stop
    return folds

def objective_wf(trial: optuna.Trial) -> float:
    lookback = trial.suggest_int("lookback", 10, 80)
    z = trial.suggest_float("z", 1.0, 3.0)
    n = len(logp)
    scores = []
    for step, (train_idx, test_idx) in enumerate(purged_folds(n, n_folds=4, embargo=30)):
        # ici : fit éventuel sur train, évaluer sur test
        sharpe, _ = backtest_mr(lookback, z, 0.5, 1.0, 2.0)
        # en vrai : restreindre backtest aux indices test
        scores.append(sharpe)
        trial.report(float(np.mean(scores)), step)
        if trial.should_prune():
            raise optuna.TrialPruned()
    return float(np.mean(scores))

study_wf = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=0),
    pruner=optuna.pruners.SuccessiveHalvingPruner(),
)
study_wf.optimize(objective_wf, n_trials=30)
```

### 15.5 Pruning mid-backtest

Voir section 7 (`fake_fx_backtest` + Hyperband). Pattern : `step` = fraction temporelle ou fold WF.

### 15.6 Persistence overnight + `load_if_exists`

```python
import optuna

STORAGE = "sqlite:///overnight_fx.db"
NAME = "eurusd_mr_v3"

def objective(trial: optuna.Trial) -> float:
    x = trial.suggest_float("x", -5, 5)
    return -(x - 1.234) ** 2

study = optuna.create_study(
    study_name=NAME,
    storage=STORAGE,
    direction="maximize",
    load_if_exists=True,
    sampler=optuna.samplers.TPESampler(seed=None),
    pruner=optuna.pruners.MedianPruner(),
)
study.optimize(objective, n_trials=20, callbacks=[optuna.study.MaxTrialsCallback(500)])
print("trials so far", len(study.trials), "best", study.best_params)

# Relancer le même script / autre machine → reprend
# Dashboard : optuna-dashboard sqlite:///overnight_fx.db
```

### 15.7 Seeds / reproductibilité

```python
import random
import numpy as np
import optuna

SEED = 123

random.seed(SEED)
np.random.seed(SEED)

sampler = optuna.samplers.TPESampler(seed=SEED)
# audits : n_jobs=1, un seul process, pas de distributed
study = optuna.create_study(direction="maximize", sampler=sampler)
study.optimize(lambda t: t.suggest_float("x", 0, 1), n_trials=10, n_jobs=1)

# Multi-process : seed=None ou seeds différents par worker pour éviter collisions
# → résultats NON bit-reproductibles entre runs distribués (doc officielle)
```

---

## 16. Checklist anti-overfitting

À traiter comme une **gate** avant de promouvoir des params vers paper/live.

| # | Contrôle | Pourquoi |
|---|---|---|
| 1 | Split temporel strict (train / validation / **hold-out final jamais touché**) | Fuite temporelle |
| 2 | Walk-forward + purged/embargo CV dans l’objective | Autocorrélation / overlapping labels |
| 3 | Coûts FX/CFD réalistes (spread, commission, slip, swap) | Edge illusoire |
| 4 | Multi-objectif return × risk + contraintes levier/marge | Un Sharpe seul ment |
| 5 | Baseline `RandomSampler` / grille naïve | Mesurer le gain du sampler |
| 6 | Importance → réduire le nombre de params | Moins de sur-ajustement |
| 7 | `PartialFixedSampler` / enqueue de la config live | Ne pas tout retuner |
| 8 | Seeds fixés + logs `user_attrs` (data version, coûts) | Auditabilité |
| 9 | Artefacts equity + configs YAML par trial | Rejouabilité |
| 10 | Méfiance envers un unique `best_trial` — regarder le plateau / Pareto | Instabilité |
| 11 | Ne pas prune sur métrique non comparable | Biais de sélection |
| 12 | Storage RDB + reprise — pas seulement un notebook éphémère | Traçabilité overnight |
| 13 | Test hold-out **une seule fois** après sélection | Pas de p-hacking |
| 14 | Paper trading avant live | Décalage exécution |

### Aide Optuna vs ce qui reste hors Optuna

| Optuna aide | Vous devez coder |
|---|---|
| Exploration efficace, pruning budget | Purged CV, coûts, microstructure |
| Pareto + contraintes | Définition du risque acceptable |
| Importance, viz, Dashboard | Interprétation métier / régimes |
| Storage rejouable | Gouvernance des données |

---


## 17. Arbre de décision — choisir la stack

Matrice **agent** : partir du type de problème, pas du sampler « à la mode ».

### 17.1 Matrice problème → stack

| Type d’algorithme / objectif | Sampler recommandé | Pruner | Storage | Notes |
|---|---|---|---|---|
| Continu dense (calib params float, faible cat) | `CmaEsSampler` | `NopPruner` ou Median si steps | SQLite→Postgres | CMA inefficient sur cat |
| Mixte cat + continu / conditional | `TPESampler(multivariate=True, group=True)` ou Hub **CatCMA** / **AutoSampler** | Median / Hyperband | RDB si long | Define-by-run naturel |
| Multi-objectif (2–3) | `TPESampler` (défaut v5) ou `NSGAIISampler` | **Aucun** (core) | RDB | Pareto via `best_trials` |
| Multi-objectif (≥4) | `NSGAIIISampler` | Aucun | RDB | Ou réduire via contraintes |
| Objectif **très cher**, ≤ ~100–500 trials | `GPSampler` ou Hub HEBO / TuRBO ; integration `BoTorchSampler` | Nop ou léger | RDB | Sample-efficient |
| Budget large (1k–10k+), continu | `CmaEsSampler` | Median/SHA si partiel | Postgres / Journal / gRPC | |
| Espace minuscule / enum | `GridSampler` / `BruteForceSampler` | Nop | InMemory OK | Exhaustif |
| Baseline anti-biais | `RandomSampler` / `QMCSampler` | Median | — | Toujours garder une baseline |
| Params partiellement figés (prod) | `PartialFixedSampler` | selon cas | RDB | + `enqueue_trial` baseline |
| Ne pas choisir | Hub `AutoSampler` | selon cas | — | deps : cmaes, torch, scipy |
| DL epochs / boosting rounds | TPE + **Hyperband** / SHA | Hyperband/SHA/Median | RDB | `report(epoch)` |
| Backtest / WF folds chers | TPE + Hyperband/SHA | idem | RDB + artefacts | `step` = fold / fraction temps |
| Contraintes hard (levier, Feller, latency) | TPE / NSGA-II / GP (+ `set_constraint`) | Nop si contrainte finale | RDB | faisable ⇔ toutes contraintes ≤ 0 |

### 17.2 Arbre rapide (texte)

```
Budget d'évaluations ?
├─ ≤ ~50–100 et objectif cher → GPSampler / BoTorch / Hub TuRBO|HEBO
├─ 100–1000 mixte/conditional → TPESampler (défaut) ; AutoSampler si indécis
├─ 1k–10k continu → CmaEsSampler
└─ exhaustif petit → Grid / BruteForce

Objectifs ?
├─ 1 → TPE / CMA / GP selon ci-dessus ; pruner OK
└─ ≥2 → TPE (défaut v5) ou NSGA-II/III ; pas de pruner core

Besoin early-stop partiel (epochs/folds) ?
├─ oui mono-obj → Hyperband (souvent + TPE) ou Median/SHA
└─ non → NopPruner

Parallélisme ?
├─ 1 process → InMemory ou SQLite
├─ multi-process même machine → Journal file ou SQLite (attention writers)
├─ multi-node → Postgres (+ heartbeat) ; très nombreux workers → gRPC proxy
└─ Dask cluster → DaskStorage (integration)
```

### 17.3 Anti-patterns

| Anti-pattern | Remplacer par |
|---|---|
| CMA-ES sur beaucoup de categoricals | TPE / CatCMA (Hub) / AutoSampler |
| Pruner en multi-obj core | Contraintes + Pareto ; ou Hub `multi_metric_pruner` |
| 30 dims libres « au cas où » | Importance → PartialFixed / réduire |
| SQLite avec 50 writers | Postgres + éventuellement gRPC proxy |
| `best_trial` pour juger la généralisation | Hold-out / WF **hors** Optuna |

---

## 18. Cheat-sheet hyperparams samplers & pruners

Params **vérifiés** sur la doc stable (constructeurs). Les args marqués dépréciés (TPE `consider_prior`, `gamma`, `weights`, … → retrait prévu v6) sont omis volontairement.

### Samplers — leviers utiles

| Classe | Params clés (défauts doc) | Quand tourner le bouton |
|---|---|---|
| `TPESampler` | `n_startup_trials=10`, `n_ei_candidates=24`, `multivariate=None`, `group=False`, `constant_liar=True`, `seed` | ↑ startup si espace bruité ; `group=True` si conditional ; `constant_liar` pour batch |
| `CmaEsSampler` | `n_startup_trials=1`, `sigma0`, `x0`, `seed`, `popsize`, `use_separable_cma`, `with_margin`, `lr_adapt`, `restart_strategy`, `source_trials` | `source_trials` = warm-start ; separable si haute dim |
| `GPSampler` | `n_startup_trials=10`, `deterministic_objective=False`, `seed`, `independent_sampler` | `deterministic_objective=True` si f sans bruit |
| `NSGAIISampler` | `population_size=50`, `crossover_prob=0.9`, `swapping_prob=0.5`, `seed`, mutation/crossover modules `optuna.samplers.nsgaii` | ↑ population si front pauvre |
| `NSGAIIISampler` | (même famille NSGA ; préférer si nombreux objectifs) | doc samplers |
| `RandomSampler` | `seed` | baseline |
| `QMCSampler` | `seed` (+ support cat depuis v5) | DOE initial |
| `GridSampler` | `search_space` dict de listes | exhaustif define-and-run |
| `BruteForceSampler` | — | exhaustif define-by-run |
| `PartialFixedSampler` | `fixed_params`, `base_sampler` | geler knobs prod |
| `BoTorchSampler` | (package **integration**) | BO avancée |
| Hub `AutoSampler` | `seed`, `constraints_func` (convention ≤0) | sélection auto ; deps torch/cmaes/scipy |

### Pruners — leviers utiles

| Classe | Params clés (défauts doc) | Effet |
|---|---|---|
| `MedianPruner` | `n_startup_trials=5`, `n_warmup_steps=0`, `interval_steps=1`, `n_min_trials=1` | coupe si pire que médiane au même step |
| `PercentilePruner` | `percentile`, `n_startup_trials=5`, `n_warmup_steps=0`, `interval_steps=1`, `n_min_trials=1` | garde le top percentile (ex. 25.0) |
| `SuccessiveHalvingPruner` | `min_resource='auto'`, `reduction_factor=4`, `min_early_stopping_rate=0`, `bootstrap_count=0` | ASHA / promotions par rungs |
| `HyperbandPruner` | `min_resource=1`, `max_resource='auto'`, `reduction_factor=3`, `bootstrap_count=0` | brackets SHA ; `max_resource` = epochs max |
| `ThresholdPruner` | `lower=None`, `upper=None`, `n_warmup_steps=0`, `interval_steps=1` | abort si hors bornes |
| `PatientPruner` | `wrapped_pruner`, `patience`, `min_delta=0.0` | tolérance avant d’accepter le prune du wrapper |
| `WilcoxonPruner` | `p_threshold=0.1`, `n_startup_steps=2` | test statistique vs best (voir tutorial Wilcoxon) |
| `NopPruner` | — | jamais prune |

**Rappel :** `trial.report(value, step)` puis `if trial.should_prune(): raise optuna.TrialPruned()`.

---

## 19. CLI Optuna

Module documenté : `optuna.cli` — `optuna --help`. Tutorial : *Command-Line Interface*.

| Commande | Rôle |
|---|---|
| `create-study` | Créer une study (retourne souvent le nom) |
| `delete-study` | Supprimer une study |
| `studies` | Lister les studies |
| `study set-user-attr` | Attribut user au niveau study |
| `ask` | Créer un trial + suggérer des params (JSON search-space) |
| `tell` | Enregistrer values / state d’un trial (ask-and-tell CLI) |
| `trials` | Lister les trials |
| `best-trial` | Afficher le best (mono-obj) |
| `best-trials` | Front de Pareto |
| `storage upgrade` | Upgrader le schéma RDB |

### Exemple interactif (lab / HITL hors Python)

```bash
# pip install optuna
STUDY_NAME=$(optuna create-study --storage sqlite:///example.db --direction minimize)

# search-space JSON : générable via
# python -c "import optuna,json; print(optuna.distributions.distribution_to_json(optuna.distributions.FloatDistribution(-10,10)))"

optuna ask --storage sqlite:///example.db --study-name "$STUDY_NAME" \
  --sampler TPESampler \
  --search-space '{"x": {"name": "FloatDistribution", "attributes": {"step": null, "low": -10.0, "high": 10.0, "log": false}}}'

# ... expérience manuelle / externe avec le x suggéré ...

optuna tell --storage sqlite:///example.db --study-name "$STUDY_NAME" \
  --trial-number 0 --values 0.7 --state complete

optuna best-trial --storage sqlite:///example.db --study-name "$STUDY_NAME"
```

**Dashboard CLI** (package séparé) : `optuna-dashboard sqlite:///example.db` — ne pas confondre avec `optuna` CLI.

---

## 20. OptunaHub — inventaire structuré

Catalogue communautaire : https://hub.optuna.org/ — packages chargés via :

```python
# pip install optunahub
import optunahub

mod = optunahub.load_module(package="samplers/auto_sampler")  # kwargs: package=...
sampler = mod.AutoSampler(seed=42)
```

Inventaire **par catégorie** (sitemap Hub, ordres de grandeur) :

| Catégorie | ~N packages | Rôle |
|---|---|---|
| `samplers/` | ~75 | Algorithmes d’échantillonnage hors / en complément du core |
| `pruners/` | ~10 | Stratégies de prune (dont variants) |
| `visualization/` | ~25 | Plots additionnels |
| `benchmarks/` | ~21 | Suites de test (BBOB, HPO-Bench, DTLZ, …) |
| `callbacks/` | ~3 | Terminator, W&B, Trackio |
| `tags/` / `optuna_versions/` | — | Navigation / compat |

### Samplers Hub — sélection utile pour un agent

| Package | Une ligne |
|---|---|
| `samplers/auto_sampler` | Choisit automatiquement un sampler selon l’espace (reco équipe Optuna) ; deps `cmaes`, `torch`, `scipy` |
| `samplers/catcma` | CMA pour espaces **mixtes** continu + discret |
| `samplers/hebo` | HEBO — BO hétéroscédastique / bruité |
| `samplers/turbo` | TuRBO — BO par régions de confiance locales |
| `samplers/hierarchical_tpe` | TPE mixture-of-experts pour search spaces **conditionnels** |
| `samplers/nsgaii_with_tpe_warmup` | NSGA-II initialisé par TPE (au lieu de Random) |
| `samplers/cmaes` / `restart_cmaes` / `cma_es_refinement` / `user_prior_cmaes` | Variantes / raffinements CMA |
| `samplers/differential_evolution` | Evolution différentielle |
| `samplers/botorch_sampler` | Variante / expo BoTorch côté Hub (le core integration a aussi `BoTorchSampler`) |
| `samplers/tpe` / `ctpe` / `multivariate_tpe_flex` | Variantes TPE / constrained TPE papier |
| `samplers/mocma` | MO-CMA-ES (multi-obj évolutif) |
| `samplers/speaii` | SPEA-II (sélection Pareto alternative à NSGA-II) |
| `samplers/pso` | Particle Swarm Optimization |
| `samplers/nelder_mead` | Nelder–Mead (init Takenaga et al. 2023) |
| `samplers/cmamae` | CMA-MAE via pyribs (quality-diversity) |
| `samplers/meta_learn_tpe` | Warm-start TPE depuis studies de tâches similaires |
| `samplers/uniform_design` | DOE uniform design (souvent mieux qu’un random naïf) |
| `samplers/smac_sampler` | SMAC3 Bayesian optimization |
| `samplers/confopt_sampler` | Quantile regression conformalisée |
| `samplers/distributionally_robust_bo` | BO robuste distributionnellement (chance constraints) |

> Ne pas tout installer : charger **un** `package="samplers/..."` à la fois. Vérifier la « Verified Optuna version » sur la fiche Hub.

### Pruners / callbacks / viz Hub (notables)

| Package | Une ligne |
|---|---|
| `pruners/conformal` | Prune avec contrôle du taux de faux positifs (conformal) |
| `pruners/multi_metric_pruner` | Contourne l’absence de prune multi-obj core via user attrs |
| `callbacks/terminator` | Arrêt auto quand l’erreur statistique le justifie (**remplace** le terminator core déprécié) |
| `callbacks/wandb` / `trackio` | Tracking expériences |
| `visualization/plot_beeswarm` | Beeswarm style SHAP params ↔ objective |
| `visualization/plot_empirical_attainment_surface` | Variabilité des fronts Pareto multi-runs (EAS) |
| `visualization/extended_pcp` | Parallel coordinates étendu (API type `plot_*`) |
| `pruners/median` / `hyperband` | Miroirs communautaires des pruners core (référence / packaging Hub) |

### Benchmarks Hub (pour tests d’algo, pas pour la prod métier)

`benchmarks/bbob`, `bbob_*`, `dtlz`, `zdt`, `wfg`, `hpolib`, `hpobench_nn`, `nasbench201`, … — utiles pour **comparer samplers**, pas comme objective métier.

### Exemple AutoSampler (Hub)

```python
import optuna
import optunahub

module = optunahub.load_module(package="samplers/auto_sampler")
study = optuna.create_study(direction="minimize", sampler=module.AutoSampler(seed=0))

def objective(trial):
    x = trial.suggest_float("x", -5, 5)
    y = trial.suggest_float("y", -5, 5)
    return x**2 + y**2

study.optimize(objective, n_trials=50)
print(study.best_params)
```

---

## 21. Exceptions utiles

Module `optuna.exceptions` (doc stable) :

| Exception | Quand |
|---|---|
| `OptunaError` | Base de toutes les erreurs Optuna |
| `TrialPruned` | À **lever** quand `should_prune()` est vrai (ou logique équivalente) pour marquer le trial PRUNED |
| `DuplicatedStudyError` | `create_study` avec un `study_name` déjà pris (`load_if_exists=False`) |
| `UpdateFinishedTrialError` | Tentative de modifier un trial déjà terminé (storage) |
| `StorageInternalError` | Erreur interne storage |
| `CLIUsageError` | Mauvaise utilisation du CLI |

```python
import optuna
from optuna.exceptions import TrialPruned, DuplicatedStudyError

def objective(trial):
    for step in range(10):
        loss = (step + 1) * trial.suggest_float("x", 0, 1)
        trial.report(loss, step)
        if trial.should_prune():
            raise TrialPruned()  # équiv. raise optuna.TrialPruned()
    return loss

storage = "sqlite:///demo_exc.db"
try:
    optuna.create_study(study_name="same", storage=storage, load_if_exists=False)
    optuna.create_study(study_name="same", storage=storage, load_if_exists=False)
except DuplicatedStudyError:
    study = optuna.load_study(study_name="same", storage=storage)
```

---

## 22. Heartbeat, stale trials & gRPC proxy

### 22.1 Heartbeat RDB (experimental)

Problème cluster : un worker meurt → trial coincé en `RUNNING` (« zombie »). Avec `constant_liar=True`, ces zombies polluent le sampling.

**API** (`RDBStorage`) :

- `heartbeat_interval` : secondes entre heartbeats (uniquement avec `optimize()`, **pas** ask/tell).
- `grace_period` : délai après dernier heartbeat avant FAIL (défaut `2 * heartbeat_interval` si `None`).
- `heartbeat_stale_trial_callback` : callback `(study, frozen_trial)` après FAIL d’un stale (préférer à `failed_trial_callback` déprécié).
- `optuna.storages.fail_stale_trials(study)` : forcer le passage RUNNING stale → FAIL.
- `RetryHeartbeatStaleTrialCallback(max_retry=None, inherit_intermediate_values=False)` : ré-enqueue automatiquement.

```python
import optuna
from optuna.storages import RDBStorage, fail_stale_trials, RetryHeartbeatStaleTrialCallback

storage = RDBStorage(
    url="sqlite:///hb_demo.db",  # en prod : postgresql+psycopg2://...
    heartbeat_interval=60,
    grace_period=120,
    heartbeat_stale_trial_callback=RetryHeartbeatStaleTrialCallback(max_retry=2),
)

study = optuna.create_study(
    study_name="cluster_job",
    storage=storage,
    direction="minimize",
    load_if_exists=True,
    sampler=optuna.samplers.TPESampler(constant_liar=True),
)

def objective(trial):
    x = trial.suggest_float("x", -5, 5)
    return x**2

# Heartbeat actif pendant optimize() uniquement
study.optimize(objective, n_trials=20)

# Maintenance manuelle possible :
fail_stale_trials(study)
```

**Pièges documentés :**

- Heartbeat **incompatible ask/tell**.
- Feature expérimentale (API peut changer).
- SQLite déconseillé pour parallélisme massif → Postgres.
- Timestamps v5 = UTC.

### 22.2 gRPC storage proxy (experimental, v4.2+)

Pour **très nombreux** workers devant un RDB : un process serveur proxyfie le storage ; les clients parlent gRPC.

```python
# --- serveur (process dédié) ---
from optuna.storages import get_storage, run_grpc_proxy_server

# Preferer get_storage(...) plutôt que RDBStorage nu (cache process)
storage = get_storage("postgresql+psycopg2://user:pass@host/db")
run_grpc_proxy_server(storage, host="0.0.0.0", port=13000)

# --- client worker ---
import optuna
from optuna.storages import GrpcStorageProxy

storage = GrpcStorageProxy(host="localhost", port=13000)
storage.wait_server_ready(timeout=60)
study = optuna.create_study(
    study_name="grpc_study",
    storage=storage,
    load_if_exists=True,
    direction="minimize",
)
# study.optimize(...)
storage.close()
```

**Limites doc :**

- **Pas** de support `JournalStorage` derrière gRPC (issue #6084) → utiliser RDB.
- Combo gRPC + SQLite3 : comportements inattendus possibles sur `delete_study` (cache).
- Feature expérimentale.

---

## 23. Patterns HPO génériques (ML / numérique / black-box)

Complète §15 (quant). Proxies pédagogiques — branchez vos vraies métriques.

### 23.1 sklearn / boosting (pruning rounds)

```python
import numpy as np
import optuna

# proxy "boosting" : loss décroissante selon n_estimators & lr
def objective(trial: optuna.Trial) -> float:
    lr = trial.suggest_float("lr", 1e-3, 0.3, log=True)
    max_depth = trial.suggest_int("max_depth", 2, 10)
    subsample = trial.suggest_float("subsample", 0.5, 1.0)
    n_estimators = trial.suggest_int("n_estimators", 50, 400)
    # simule des rounds
    loss = 1.0
    for step in range(0, n_estimators, max(1, n_estimators // 10)):
        loss = 0.2 + abs(lr - 0.05) + abs(max_depth - 5) * 0.01 + (1 - subsample) * 0.05
        loss *= 0.9 ** (step / max(n_estimators, 1))
        trial.report(loss, step)
        if trial.should_prune():
            raise optuna.TrialPruned()
    trial.set_user_attr("family", "boosting_proxy")
    return float(loss)

study = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.TPESampler(seed=0),
    pruner=optuna.pruners.HyperbandPruner(min_resource=1, max_resource=400, reduction_factor=3),
)
study.optimize(objective, n_trials=30)
```

Intégrations réelles : `LightGBMPruningCallback`, `XGBoostPruningCallback`, `OptunaSearchCV` (`optuna-integration`).

### 23.2 Deep learning — prune par epoch

```python
import optuna

def objective(trial: optuna.Trial) -> float:
    lr = trial.suggest_float("lr", 1e-5, 1e-1, log=True)
    hidden = trial.suggest_int("hidden", 32, 512, log=True)
    # proxy training loop
    val = 2.0
    for epoch in range(20):
        val = 0.5 + abs(lr - 1e-3) * 10 + abs(hidden - 128) / 256
        val *= 0.85 ** epoch
        trial.report(val, epoch)
        if trial.should_prune():
            raise optuna.TrialPruned()
    return val

study = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.TPESampler(seed=0, multivariate=True),
    pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=2),
)
study.optimize(objective, n_trials=25)
```

### 23.3 Black-box numérique (calib / simulation)

```python
import optuna

def objective(trial: optuna.Trial) -> float:
    # params physiques / ingénierie
    a = trial.suggest_float("a", 1e-3, 10.0, log=True)
    b = trial.suggest_float("b", -1.0, 1.0)
    c = trial.suggest_float("c", 0.0, 5.0)
    # RMSE proxy vs cibles
    target = (0.5, 0.0, 2.0)
    err = (a - target[0]) ** 2 + (b - target[1]) ** 2 + (c - target[2]) ** 2
    return float(err)

study = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.CmaEsSampler(seed=0, n_startup_trials=5),
    pruner=optuna.pruners.NopPruner(),
)
study.optimize(objective, n_trials=60)
```

### 23.4 Nested / conditional search

Voir §5 exemple `signal` zscore/breakout. Pattern générique :

```python
def objective(trial):
    algo = trial.suggest_categorical("algo", ["svm", "rf"])
    if algo == "svm":
        c = trial.suggest_float("svc_c", 1e-3, 1e3, log=True)
        gamma = trial.suggest_float("svc_gamma", 1e-4, 1e0, log=True)
        score = -(abs(c - 1.0) + abs(gamma - 0.01))
    else:
        depth = trial.suggest_int("rf_depth", 2, 16)
        n_est = trial.suggest_int("rf_n", 50, 300)
        score = -(abs(depth - 8) + abs(n_est - 100) / 100)
    return score

study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=0, multivariate=True, group=True),
)
```

### 23.5 Warm-start depuis des runs antérieurs

```python
import optuna
from optuna.trial import create_trial, TrialState
from optuna.distributions import FloatDistribution, IntDistribution

study = optuna.create_study(direction="minimize")

# 1) Résultats déjà connus (autre campagne / grille)
study.add_trial(
    create_trial(
        params={"lr": 0.01, "depth": 5},
        distributions={
            "lr": FloatDistribution(1e-4, 1e-1, log=True),
            "depth": IntDistribution(2, 10),
        },
        value=0.42,
        state=TrialState.COMPLETE,
    )
)

# 2) Config à (re)évaluer en priorité
study.enqueue_trial({"lr": 0.01, "depth": 5})

def objective(trial):
    lr = trial.suggest_float("lr", 1e-4, 1e-1, log=True)
    depth = trial.suggest_int("depth", 2, 10)
    return abs(lr - 0.01) + abs(depth - 5) * 0.01

study.optimize(objective, n_trials=20)
```

### 23.6 Batch ask / tell

```python
import optuna
from optuna.trial import TrialState

study = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.TPESampler(seed=0, constant_liar=True),
)

BATCH = 4
for _ in range(5):
    trials = [study.ask() for _ in range(BATCH)]
    # évaluation parallèle / vectorisée externe
    values = []
    for t in trials:
        x = t.suggest_float("x", -5, 5)
        values.append(x**2)
    for t, v in zip(trials, values):
        study.tell(t, v)

print(study.best_params, study.best_value)
```

### 23.7 Transfert / meta warm-start (Hub)

Pour réutiliser des studies de **tâches voisines** (même famille d’algo, autre dataset) : Hub `samplers/meta_learn_tpe` (TPE meta-learn). Alternativement, pattern core simple = `add_trial` / `enqueue_trial` depuis un export JSON de `study.best_trials` (§23.5).

### 23.8 Checklist express avant `optimize`

1. Direction(s) correctes ? Contraintes en `set_constraint` (≤ 0) ?
2. Sampler adapté à l’espace (§17) + `seed` si audit ?
3. Pruner mono-obj seulement + `report` sur une métrique comparable ?
4. Storage partagé si multi-worker / Dashboard ?
5. `user_attrs` : version data/code, coûts, seed objective ?
6. Budget : `n_trials` / `timeout` / `MaxTrialsCallback` ?
7. Protocole anti-overfitting (§16) figé **avant** de regarder le hold-out ?

---

## 24. Ce qu’il ne faut **pas** optimiser avec Optuna

Optuna brille en **black-box** (pas de gradient fiable, espace mixte, evaluations chères/bruitées). Ce n’est pas le bon outil par défaut si :

| Situation | Préférer plutôt |
|---|---|
| MLE / MAP d’un modèle à loglik lisse (GLM, beaucoup de GARCH via `arch`, calibrations convexes) | Optimiseurs gradient / Newton (`scipy.optimize`, Autograd/JAX) |
| Problème **convexe** connu (lasso, SVM dual bien posé, QP) | Solveurs convex (OSQP, CLARABEL, CVXPY) |
| MIP / scheduling / knapsack / contraintes logiques dures | OR-Tools, Gurobi, HiGHS, CP-SAT |
| Différentiable bout-en-bout (réseau + loss) pour *apprendre* les poids | SGD/Adam — Optuna seulement pour **HPO** (lr, arch) |
| Recherche exacte sur grille minuscule déjà énumérable à la main | Boucle for / `GridSampler` si vous voulez le logging Optuna |
| Tuning « une fois » de 2 params évidents | Grille ou Random suffit ; Optuna = overhead inutile |

**Quand même Optuna pour la calib :** dès que la vraisemblance est non-diff / bruyante / hybride (cat + continu), ou que vous encapsulez un simulateur externe (Monte Carlo, backtest, lab).

---

## 25. Références officielles

| Ressource | URL |
|---|---|
| Optuna docs (stable) | https://optuna.readthedocs.io/en/stable/ |
| API index | https://optuna.readthedocs.io/en/stable/reference/index.html |
| Samplers | https://optuna.readthedocs.io/en/stable/reference/samplers/index.html |
| TPESampler | https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html |
| Pruners | https://optuna.readthedocs.io/en/stable/reference/pruners.html |
| Search space | https://optuna.readthedocs.io/en/stable/reference/search_space.html |
| Exceptions | https://optuna.readthedocs.io/en/stable/reference/exceptions.html |
| CLI (`optuna.cli`) | https://optuna.readthedocs.io/en/stable/reference/cli.html |
| Tutorial CLI | https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/004_cli.html |
| Visualization | https://optuna.readthedocs.io/en/stable/reference/visualization/index.html |
| Storages | https://optuna.readthedocs.io/en/stable/reference/storages.html |
| `RDBStorage` / heartbeat | https://optuna.readthedocs.io/en/stable/reference/generated/optuna.storages.RDBStorage.html |
| `fail_stale_trials` | https://optuna.readthedocs.io/en/stable/reference/generated/optuna.storages.fail_stale_trials.html |
| `GrpcStorageProxy` | https://optuna.readthedocs.io/en/stable/reference/generated/optuna.storages.GrpcStorageProxy.html |
| `run_grpc_proxy_server` | https://optuna.readthedocs.io/en/stable/reference/generated/optuna.storages.run_grpc_proxy_server.html |
| Study API | https://optuna.readthedocs.io/en/stable/reference/study.html |
| Trial API (`set_constraint`, `suggest_*`) | https://optuna.readthedocs.io/en/stable/reference/generated/optuna.trial.Trial.html |
| Ask-and-Tell | https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/009_ask_and_tell.html |
| Multi-objective | https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/002_multi_objective.html |
| Importance | https://optuna.readthedocs.io/en/stable/reference/importance.html |
| Artifacts | https://optuna.readthedocs.io/en/stable/reference/artifacts.html |
| Logging | https://optuna.readthedocs.io/en/stable/reference/logging.html |
| Terminator (déprécié core) | https://optuna.readthedocs.io/en/stable/reference/terminator.html |
| optuna-integration docs | https://optuna-integration.readthedocs.io/en/stable/ |
| optuna-integration GitHub | https://github.com/optuna/optuna-integration |
| Optuna Dashboard | https://optuna-dashboard.readthedocs.io/en/latest/ |
| Dashboard Getting Started | https://optuna-dashboard.readthedocs.io/en/latest/getting-started.html |
| Dashboard HITL | https://optuna-dashboard.readthedocs.io/en/latest/tutorials/hitl.html |
| Dashboard preferential | https://optuna-dashboard.readthedocs.io/en/latest/tutorials/preferential-optimization.html |
| Dashboard LLM | https://optuna-dashboard.readthedocs.io/en/latest/tutorials/llm-integration.html |
| OptunaHub | https://hub.optuna.org/ |
| AutoSampler | https://hub.optuna.org/samplers/auto_sampler/ |
| Hub terminator callback | https://hub.optuna.org/callbacks/terminator/ |
| Release v5.0.0 | https://github.com/optuna/optuna/releases/tag/v5.0.0 |
| Rustuna (related) | https://rustuna.readthedocs.io/en/latest/ |

---

### Projets connexes (hors inventaire API détaillé)

| Projet | Rôle | Doc |
|---|---|---|
| **Optuna Dashboard** | UI, HITL, preferential, notes, LLM helpers | §13 + docs Dashboard |
| **OptunaHub** | Catalogue samplers/pruners/viz/benchmarks/callbacks | §20 |
| **optuna-integration** | Callbacks ML + BoTorchSampler + DaskStorage | §14 |
| **Rustuna** | Projet lié (écosystème Optuna / Rust) — ne pas inventer d’API ici | https://rustuna.readthedocs.io/en/latest/ |

### Notes de rédaction

- Inventaire calé sur le document du 2026-10-25, enrichi le **2026-10-26** après fetch des pages stables listées ci-dessus + sitemap Hub + release `v5.0.0`.
- Exemples = **proxies** éducatifs : branchez votre trainer / backtester / simulateur, vos coûts et votre CV réelle.
- Si une API change après la date du document, se fier à `optuna.readthedocs.io/en/stable/` et au changelog GitHub.
- Projets connexes cités sans API inventée : **Optuna Dashboard**, **OptunaHub**, **optuna-integration**, **Rustuna**.

*Fin du document — Optuna inventaire enrichi (spectre complet pour agents HPO).*
