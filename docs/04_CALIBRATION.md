# 04 — Calibration Optuna v2

Campagne : `poc_exec_v2` (TPE) + `poc_exec_rand_v2` (RandomSampler de
comparaison) · 12,07 h · moteur R7 · SQLite `optuna.db`.

## 1. Protocole (inventaire Optuna appliqué)

| Élément | Choix |
|---|---|
| Split temporel | jours 22+23 **in-sample** (2 steps de pruning) · jour 26 **hold-out évalué UNE fois — sur la pile NautilusTrader, pas ici** |
| Objectif | P&L cumulé exécuté (masse d'opportunité, pas P&L de compte) |
| Sampler | TPE multivarié, seed fixe, `n_jobs=1` ; baseline `enqueue_trial` (défauts du document) |
| Pruner | MedianPruner (`n_startup_trials=8`, warmup 0) — P&L cumulé comparable entre trials |
| Contraintes | `min_trades ≥ 10` · `abort_rate ≤ 30 %` (`set_constraint` ≤ 0, API v5) |
| Comparaison | RandomSampler 37 trials — quantifie la valeur du TPE |
| Reproductibilité | RDB SQLite + `load_if_exists`, reprise à chaud |
| Hawkes MLE | **hors Optuna** — estimateur dédié sur les épisodes |
| PedAnova | `get_param_importances(study, target=lambda t: -t.value)` — target **négatif** car maximise |

## 2. Espace des 8 paramètres

| Paramètre | Borne | Rôle |
|---|---|---|
| `fresh_ms` | [100, 2000] log | âge max d'une quote valide |
| `spread_cap` | [1,2, 3,0] | rejet des spreads > cap × médian |
| `comm_scale` | [1,0, 1,5] | multiplicateur de commission — **planché à 1,0 : jamais sous le taux négocié 3 $/côté** |
| `ddd_min` | [0, 40] | marge nette minimale d'envoi ($) |
| `lat_ms` | [1, 10] | latence par jambe — **large volontairement** (production sub-ms → stress conservateur) |
| `tau_slip_bp` | [0,3, 5,0] log | mouvement toléré entre envoi et fill (IOC) |
| `q_min` | [0,1, 0,95] | fraction minimale de fill acceptée |
| `cooldown_s` | [0, 120] | verrou anti-rechevauchement |

## 3. Résultats de la campagne

| | TPE (150) | Random (37) |
|---|---|---|
| Best | **1 131 006 $** (T0 baseline doc) | 97 539 $ |
| Médiane complet | **148 122 $** | 7 367 $ |
| Complétion | 74 ok / 76 élagués | 13 ok / 24 élagués |

La médiane ~20× démontre que l'espace des paramètres est hostile au hasard —
le TPE cartographie une vraie structure, pas du bruit.

## 4. Signature de la zone gagnante (top trials)

| Param | Convergence |
|---|---|
| `lat_ms` | **1,3-2,5 ms** — la vitesse est nécessaire (production ≤1 ms favorable) |
| `fresh_ms` | 0,9-1,2 s |
| `ddd_min` | 1,7-3,6 $ |
| `tau_slip_bp` | 0,54-0,73 bp |
| `q_min` | 0,16-0,24 |
| `cooldown_s` | 0,05-0,5 s |
| `comm_scale` | 1,04-1,12 (3,1-3,4 $/côté — pessimiste vs négocié) |
| `spread_cap` | 1,6-1,76 |
| aborts | 0-4 sur 60-190 k trades |

## 5. PedAnova — importance relative

```
cooldown_s   0,307  ━━━━━━━━━━━━━━━━━━━━  (débit / sérialisation)
spread_cap   0,156  ━━━━━━━━
ddd_min      0,144  ━━━━━━━
tau_slip_bp  0,116  ━━━━━━
q_min        0,096  ━━━━━
lat_ms       0,084  ━━━━
fresh_ms     0,071  ━━━
comm_scale   0,025  ━
```

**Lecture honnête** : sur l'objectif « masse captée », le cooldown domine par
construction (il borne le débit de trades). `lat_ms` n'est que 6ᵉ *sur cette
métrique* — mais tous les tops convergent vers la latence basse : la vitesse
est **nécessaire sans être suffisante**. Pour la marge unitaire, voir
[05_RESULTATS.md](05_RESULTATS.md) : ~0,65 bp/trade quasi plate sur tout le top.

## 6. Incidents d'apprentissage (journalisés)

| Bug | Effet | Correctif |
|---|---|---|
| R6 directions fantômes | chemins orientés base→quote systématique | `p_start` = nœud de départ réel du DFS |
| R7 fuite d'état inter-trials | `pos` non-rembobinable + quote future + fraîcheur unilatérale → P&L fantôme ~10,5 M$ | rembobinage + garde `ts[j]≤t` + fraîcheur bilatérale ; tests T1-T6 ; DB archivée `buggy_r2` |

Ces corrections sont la raison pour laquelle la campagne v2 est la première
dont les résultats sont exploitables.
