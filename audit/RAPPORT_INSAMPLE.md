# Rapport intermédiaire in-sample — POC arbitrage FX
**Convention A · univers FX pur (28 paires) · jours 22+23 oct. 2026 · jour 26 réservé (pile NautilusTrader)**

Campagne : `poc_exec_v2` 150 trials TPE + 37 RandomSampler · 12,07 h · moteur R7
Source : `/tmp/report_insample.json` (replay best params, exec_seq instrumenté)

## 1. Candidats détectés (graphe complet, 1 059 069 évals)

| Métrique | Valeur |
|---|---|
| Candidats | 1 029 811 |
| net > 0 / > 1 / > 5 / > 10 / > 20 / > 50 $ | 335 178 · 311 616 · 282 325 · **17 321** · 59 · 1 |
| net médian / p90 / p99 / p99.9 / max ($) | −1,99 · 7,22 · 11,74 · 15,05 · **120,21** |
| net bp : p50 / p90 / p99 | −0,20 · 0,72 · 1,17 |
| Chemins argmax par jambes | **1 jambe 997 697 (96,9 %) · 2 jambes 31 773 (3,1 %) · 3 jambes 341 (0,03 %)** |
| Chemins à terminaison bout | **0 (dominés sous Convention A — pureté FX garantie)** |

## 2. Marges par paire terminale (top)

| Paire | n | méd. $ | méd. pips |
|---|---|---|---|
| GBPCAD | 89 770 | **+5,75** | **+1,08** |
| CADJPY | 122 687 | +2,30 | +0,26 |
| EURCAD | 298 736 | +0,26 | +0,04 |
| AUDCAD | 3 948 | +0,39 | +0,04 |
| AUDCHF | 73 | +2,11 | +0,12 |
| NZDJPY | 204 049 | −1,23 | −0,11 |
| USDJPY | 89 156 | −3,96 | −0,62 |
| EURUSD | 74 630 | −3,57 | −0,40 |
| USDCHF | 9 411 | −7,86 | −0,65 |
| NZDUSD | 4 057 | −14,06 | −0,80 |

Les médianes négatives = le scan garde aussi les quasi-candidats sous le seuil
(`thresh −30 $`). La sélection effective passe par `ddd_min` calibré.

## 3. Persistance des épisodes

22 344 épisodes · médiane **0,296 s** · p90 **7,25 s** · p99 **29,0 s** ·
max légitime 335 s · **1 artefact week-end (51 h) identifié et filtré**.

## 4. Exécution détaillée — replay best params (baseline doc)

Params : `fresh 500 ms · cap 1,7 · comm 1,0 (3 $/côté) · ddd 0 · lat 2,5 ms ·
slip 0,8 bp · q_min 0,5 · cooldown 0`.

| Jour | Trades | P&L | Win rate | Aborts | Fills 1/2/3 | Partiels |
|---|---|---|---|---|---|---|
| 22 oct. | 169 904 | +1 110 095 $ | 99,93 % | 0 | 155 320 / 14 377 / 207 | 9 jambes |
| 23 oct. | 16 199 | +20 911 $ | 97,85 % | 4 (kill IOC) | 15 990 / 204 / 1 | 12 jambes |
| **Total** | **186 103** | **+1 131 006 $** | **99,76 %** | 4 (0,002 %) | — | **21 jambes** |

### Par nombre de jambes

| Jambes | Trades | P&L | Win rate | Durée méd. | Aborts |
|---|---|---|---|---|---|
| 1 | 171 314 | +1 019 286 $ | 99,75 % | **2,5 ms** | 4 |
| 2 | 14 581 | +109 924 $ | 99,72 % | **5,0 ms** | 0 |
| 3 | 208 | +1 796 $ | 98,08 % | **7,5 ms** | 0 |

## 5. Faisabilité temporelle des séquences — réponse

- Chaîne 3 jambes ≈ **7,5 ms** d'exécution vs persistance médiane des épisodes
  **296 ms** → marge temporelle ~40× en médiane, ~1 000× au p90.
- 0 abort sur les chemins multi-jambes ; les 4 `kill IOC` surviennent sur des
  1-jambe (jour 23, régime plus volatil).
- L'optimiseur converge `lat_ms` → 1,3-2,5 ms : la production ≤1 ms est
  **en dessous** de la zone calibrée gagnante.
- Le facteur limitant est la concurrence (capturer avant le marché), pas la
  survie du gap — confirmé par PedAnova (cooldown/spread_cap dominant).

## 6. Calibration (Optuna v2)

- Best : T0 baseline doc 1,131 M$ (mass capture) · T87 901 k$ · T104 698 k$
- Marge/transaction **~0,61-0,68 bp** (~6,1-6,8 $ / 100 K) quasi plate sur tout
  le top — la sélectivité coupe le volume sans écraser la marge.
- TPE vs hasard : médiane 148 k$ vs 7,4 k$ (~20×).
- PedAnova : cooldown_s 0,31 · spread_cap 0,16 · ddd_min 0,14 · tau_slip 0,12 ·
  q_min 0,10 · lat_ms 0,08 · fresh_ms 0,07 · comm_scale 0,03.
- Convergence : lat 1,3-2,5 ms · fresh 0,9-1,2 s · ddd_min 1,7-3,6 ·
  slip 0,54-0,73 bp · comm_scale 1,04-1,12.

## 7. Limites honnêtes

- In-sample uniquement (jours 22+23) ; jour 26 réservé à la pile NautilusTrader.
- Objectif = masse d'opportunité exécutée, pas P&L de compte (pas de contrainte
  de capital ni d'inventaire net).
- Garde volume top-of-book non implémentée au POC (les ticks FX affichent
  ~0,3-2 M$ — généralement suffisant à 100 K$, à spécifier dans le cœur Rust).
- Marquage résiduel mid FX (convention doc) — léger biais ~demi-spread.
- Jour 23 structurellement plus calme (−88 % de trades) : régimes à suivre.
