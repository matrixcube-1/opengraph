# 05 — Résultats in-sample vérifiés

Chiffres consolidés de `audit/RAPPORT_INSAMPLE.md` + `holdout.json`
(complétion 150/150) + `report_insample.py` (replay instrumenté).
Jours 22+23 oct. 2026 — **jour 26 non évalué ici** (réservé pile NT).

## 1. Candidats et distribution des marges

```
scan graphe complet : 1 029 811 candidats · 22 344 épisodes

   P&L théorique DDD            net_bp théorique
   p50   −1,99 $                p50   −0,199 bp
   p90   +7,22 $                p90   +0,722 bp
   p99  +11,74 $                p99   +1,174 bp
   max  +120,21 $               max   +12,02 bp
   >10 $ : 17 321 candidats     >20 $ : 59
```

La masse d'opportunité exécutable est réelle : ~1,7 % des candidats dépassent
10 $, la queue atteint 120 $ (≈12 bp) — les gaps >0,9 pip existent en volume.

## 2. Réconciliation déterministe

Ré-exécution jambe par jambe avec l'état d'épisode réel (`decompose_trade.py`):
différence **0,0000 $** — `eval_at` (théorie) et `exec_seq` (pratique)
concordent exactement à fill complet.

## 3. Marges par paire terminale (top médianes)

| Paire | Occurrences | P&L médian | **pips** |
|---|---|---|---|
| GBPCAD | 2 516 | +10,80 $ | **+1,079** |
| NZDCHF | 1 344 | +8,14 $ | +0,813 |
| EURCHF | 1 504 | +7,67 $ | +0,767 |
| GBPCHF | 1 815 | +7,35 $ | +0,735 |
| AUDCHF | 1 905 | +6,89 $ | +0,689 |
| NZDCAD | 2 222 | +6,68 $ | +0,668 |
| AUDCAD | 2 640 | +6,52 $ | +0,652 |
| EURCAD | 2 797 | +6,51 $ | +0,651 |
| AUDNZD | 2 508 | +6,46 $ | +0,646 |
| GBPNZD | 2 538 | +6,24 $ | +0,624 |
| … | | | |
| CADJPY | 6 255 | +3,01 $ | +0,264 |
| EURUSD | 7 391 | +0,36 $ | +0,036 |

La queue haute : GBPCAD p90 +41,7 $ (+4,17 pips) · NZDCHF p90 +37,9 $ (+3,79)
· GBPNZD p90 +38,0 $ (+3,80). Les majors directs (EURUSD, AUDUSD, EURJPY)
affichent des marges médianes quasi nulles — **l'alpha vit dans les
croisements**, pas dans les paires les plus compétées.

## 4. Épisodes et concurrence

```
épisodes mesurés : médiane 0,296 s · p90 7,2498 s · p99 28,97 s
                   max légitime 335,1 s · artefact week-end : 183 600 s (filtré)

durée d'exécution : 2,5 ms × L  →  marge médiane ~40× à 3 jambes
```

## 5. Exécution réelle — replay baseline T0

| Métrique | Valeur |
|---|---|
| Trades exécutés | **186 103** |
| P&L agrégé | **+1 131 005,54 $** |
| Wr global | **99,76 %** (185 634 W / 465 L) |
| Aborts | **4** (3 no quote, 1 kill IOC) sur 2 jours |
| Jambes partielles | **21** (9+12) — résiduel cash propre |
| $/trade | 6,08 → **0,61 bp** |

### Profil par nombre de jambes

| L | Trades | P&L | Wr | Durée |
|---|---|---|---|---|
| 1 | 171 314 | 1 020 k$ | 99,73 % | 2,5 ms |
| 2 | 14 581 | 105 k$ | 99,73 % | 5,0 ms |
| 3 | 208 | 6,5 k$ | 98,08 % | 7,5 ms |

**0 abort sur multi-jambes** — la fenêtre temporelle n'est pas le goulot.
92 % des trades sont à 1 jambe : le marché offre surtout des mispricing
directs de paires croisées ; les chemins 2-3 jambes existent, fonctionnent,
et l'optimiseur les choisit quand leur marge le justifie.

### Marge unitaire vs volume

| Trial | P&L | Trades | $/trade | bp |
|---|---|---|---|---|
| T0 | 1 131 k$ | 186 k | 6,08 | 0,61 |
| T87 | 902 k$ | 133 k | 6,76 | 0,68 |
| T104 | 698 k$ | 105 k | 6,66 | 0,67 |
| T149 | 683 k$ | 102 k | 6,70 | 0,67 |

La marge par trade est **quasi plate ~0,65 bp** quelle que soit la
sélectivité — comportement d'un gap de marché réel, pas d'un artefact de
sur-sélection.

## 6. Limites (à lire avant toute conclusion)

- **In-sample** : jours 22+23 seulement ; le jour 26 dira si ça généralise.
- **Masse d'opportunité, pas P&L de compte** : pas de contrainte de capital,
  d'inventaire ni de sérialisation multi-trades — la capacité réelle
  dépendra du sizing.
- **Volume** : fills au notionnel complet ; la garde top-of-book est un
  point spécifié pour le cœur Rust (impact nul attendu à 100 K$ sur FX).
- **Concurrence externe** : le modèle voit le tick brut — la part du gap
  réellement capturable par un arriveur non prioritaire est à mesurer en
  conditions réelles (gates de latence du runbook).
- **Événements** : 3 jours ne couvrent ni news, ni rollovers, ni month-end —
  l'échantillon large (calibration 2025-01→2026-09) est le vrai test.

## 7. Verdict in-sample

La mécanique est saine : fraîcheur bilatérale, fills aux vrais ticks,
partiels marqués, aborts journalisés, réconciliation nulle. Le signal est
réel et persistant au-delà du budget d'exécution. La question ouverte —
capacité et robustesse hors échantillon — passe par le runbook
NautilusTrader, pas par plus de calibration Python.

## 8. Addendum — venue réaliste NautilusTrader (chiffres qui comptent)

Les chiffres ci-dessus sont du modèle rapide `exec_seq` (références de
palier). Sur la **venue NT complète** (`--mode real`, lat 5 ms, IOC L1,
haircut de file `h = spr·(0,1 + 1,0·frac)`, `ddd_min = 0` figé) :

| Jour | Execs | PnL | Win rate | $/exec |
|---|---|---|---|---|
| 22/10 | 10 010 | +48 392,94 $ | 94,5 % | +4,83 |
| 23/10 | 1 050 | −627,97 | 27,9 % | −0,60 |
| 26/10 | 206 | −58,57 | 40,8 % | −0,28 |
| 27/10 (holdout) | 134 | −60,65 | 34,3 % | −0,45 |
| **Total** | **11 400** | **+47 646** | | |

Le 22/10 était un jour de choc macro (panique IA, −19 pb sur les yields
US, pétrole, JPY refuge) : 96 % des execs partaient de CAD
(`EURCAD`+`CADJPY`+`GBPCAD` = 92 % du PnL). Les jours calmes saignaient
sur distorsions sous-marginales (net médian 0,69-0,86 $ < friction).

**Sous `ddd_min = 2,5` (balayage flux venue, plateau 2-4 $)** :
jours in-sample tous positifs (49 179 / +362 / +56) ; holdout post-hoc
+11,25 / 6 execs vs −60,65 / 134. Détail : [07_REGIME_SEUIL.md](07_REGIME_SEUIL.md)
§5-6 ; relevé complet `audit/statement_flow.pdf` + `statement_trades.csv`.
