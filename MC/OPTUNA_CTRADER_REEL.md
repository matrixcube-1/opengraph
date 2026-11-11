# Optuna v5 sur ticks réels cTrader — 11/11/2026

## Protocole (règles établies)

- **Données** : ticks réels des journaux cBot — jamais de générateur.
  In-sample = 02 + 03/11 (`arb_cs_journal`, steps du MedianPruner) ;
  holdout = 04/11 évalué **une fois** ; second OOS = `arb_og_trade`
  10-11/11 (non évalué — déjà prouvé 0 opportunité par le replay MC).
- **Sampler** : TPE multivarié seed 42, n_jobs=1, `MedianPruner`
  (n_startup=10) ; étude Random comparée même budget réduit ;
  `enqueue_trial` des défauts live ; SQLite `optuna_ctrader_reel.db`
  + `load_if_exists` ; **contraintes v5 `trial.set_constraint`** :
  min_trades ≥ 10 et abort_rate ≤ 30 %.
- **Espace** : fresh_ms [2k–30k log], spread_cap [1.0–3.0],
  comm_scale [1.0–1.3], ddd_min [0–1.0], tau_slip_bp [1.0–4.0],
  q_min [0.3–0.9]. Latence fixée 280 ms et comm 0.40 $/side
  (propriétés mesurées du venue — pas des hyperparamètres).
- **Objectif** : PnL exécuté in-sample (exec_seq déterministe, K=1) ;
  moteur `Engine.live()` ingéré une fois par jour, rembobiné par
  `sync` — verrous identiques au live (warm-up, lock panier, gap+hold).

## Résultat — région faisable VIDE

| Étude | Essais terminés | Prunés | Faisables |
|---|---|---|---|
| TPE | 303 | 216 | **0** |
| Random | 102 | 0 | **0** |

**Plafond dur : 5 trades / 2 j in-sample sur tout l'espace** (405 essais).
Distribution TPE : 273×0, 23×3, 5×4, 2×5. Random : 79×0, 13×2, 8×3, 2×4.

Les deux seules combinaisons à 5 trades :
- #11 : +3,78 $ mais **60 % aborts** — ddd_min 0.15, fresh 16,4 s
- #6 : −0,65 $, **40 % aborts** — ddd_min 0.18, fresh 19,1 s

→ Atteindre 5 exige déjà de doubler la tolérance de fraîcheur
(16–19 s vs 8 s live = zone de quotes fantômes) ET d'accepter
40–60 % d'aborts. Personne n'approche les 10.

## Holdout (éval unique, params live — pas de sélection)

Params déployés (fresh 8000, cap 1.76, scale 1.10, ddd 0.4, tau 2.08,
q 0.5) sur 04/11 : **−3,13 $, 1 trade, 0 abort**.
In-sample sous les mêmes params : 4 trades, −0,21 $, 0 % abort.

## PedAnova

`q_min` 0.28, `spread_cap` 0.26, `fresh_ms` 0.22, `tau_slip_bp` 0.20,
`ddd_min` 0.04, `comm_scale` 0.01 — sur essais majoritairement
infaisables : ces importances décrivent ce qui module le **nombre de
trades**, pas le profit (il n'y a pas de profit à expliquer).

## Verdict

Le problème n'est pas le réglage : **le feed réel ne contient pas assez
de moments exécutables**. ~13 évals positives sur le 03/11 se réduisent
à ~5 instants tradables (verrou panier + gap/hold) et s'exécutent mal
(40–60 % aborts IOC/partiels/stale). Optuna poussé au maximum mesure le
plafond : **≤ ~2,5 trades/jour à 10K, PnL ~0 à −3 $, sous n'importe quel
réglage admissible**. La viabilité ne dépend pas des paramètres — elle
dépend de la richesse en désynchronisations du venue → UAT FIX/ECN.

Artefacts : `audit/optuna_ctrader_reel.py`, `audit/optuna_ctrader_reel.db`,
`MC/optuna_ctrader_reel.json`.
