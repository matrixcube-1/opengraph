# 03 — Exécution séquentielle E1-E9

Le DDD détecté est **théorique** ; le P&L réalisable dépend de l'exécution
séquentielle des jambes à des **vrais ticks ultérieurs**. Référence :
`poc_engine.Engine.exec_seq` / `_abort`.

## 1. Diagramme

```
détection t0 : eval_at → best_net ≥ ddd_min, chemin p sélectionné
        │
        ▼  qty = NOTIONAL / up[start] ; cash = 0 ; comm = 0
┌─────────────────────────────────────────────────────────────┐
│ pour chaque jambe k (séquentiel, JAMAIS atomique) :          │
│                                                             │
│   t_fill = t_prev + lat_ms                                  │
│   q_send = quote_at(sym, t_prev)     (quote d'envoi)        │
│   q_now  = quote_at(sym, t_fill)     (VRAI tick ≤ t_fill)   │
│                                                             │
│   j < 0 ou q_send invalide ──────────────► ABORT "no quote" │
│                                                             │
│   move = |q_now/q_send − 1|                                 │
│   ┌─────────────────┬──────────────────┬────────────────┐   │
│   │ move ≤ slip     │ slip<move≤3·slip │ move > 3·slip  │   │
│   │ frac = 1.0      │ frac = 0.6       │ ABORT "kill    │   │
│   │ (fill complet)  │ (partiel)        │ IOC"           │   │
│   └─────────────────┴──────────────────┴────────────────┘   │
│                                                             │
│   frac < q_min ────────────────────────► ABORT "q<q_min"    │
│                                                             │
│   si partiel : cash += qty(1−frac) × up[node]   (mid)       │
│   qty   *= frac × q_now                                     │
│   comm  += COMM / up[base(e)]                               │
│   node   = autre côté de l'arête ;  t = t_fill              │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
usd_end = qty × up[node_final]
pnl     = usd_end + cash − NOTIONAL − comm
        │
        └── durée totale = Σ lat_ms par jambe (séquence ~2,5·L ms)
```

## 2. Abort — coût et sémantique

```
pnl_abort = −12 $ × jambes_remplies + cash − comm
```

Proxy de débouclage : chaque jambe déjà remplie doit être dénouée
(demi-spread + commission ≈ 12 $). Les raisons distinctes sont journalisées :
`no quote` · `kill IOC` · `q<q_min` · (`bout stale` — inactif sous Conv. A).

## 3. Ce que l'exécution vérifie — et ce qu'elle ne fait pas

| Fait | Détail |
|---|---|
| fill au **vrai tick** | `quote_at` = dernier tick ≤ t_fill via dichotomie — jamais la quote de détection, jamais le futur |
| DDD recalculé par jambe | chaque fill voit la nouvelle quote — le mouvement adverse est capté par les portes IOC |
| partiels traqués | `parts` compte les jambes à frac<1 (résiduel marqué mid au cash) |
| fraîcheur bilatérale | `last_t ≤ t` **et** `t − last_t ≤ fresh_ms` — un quote « fraîche » ne peut pas être future |
| sérialisation | `t_free` (dur + cooldown) empêche le rechevauchement des trades dans le replay |

| Non modélisé (écart assumé) | Impact |
|---|---|
| volume top-of-book | fill systématique à 100 % du notionnel — ticks FX ~1-3 M, bénin à 100 K$ ; **garde volume spécifiée dans le Rust** (unités CFD par classe) |
| rejet broker/REST | pas de refus côté venue ; les kills IOC simulent l'échec de prix |
| concurrence externe | le gap peut être capté par d'autres acteurs — le modèle voit le tick brut, pas la course |

## 4. Budget temporel — la question qui compte

```
durée d'une séquence = nb_jambes × lat_ms   (calibré lat 1,3-2,5 ms)

   1 jambe : ~2,5 ms        2 jambes : ~5,0 ms        3 jambes : ~7,5 ms

persistance des épisodes : médiane 296 ms · p90 7,25 s · p99 29 s

marge temporelle médiane : ~40× (3 jambes) — ~1 000× au p90
```

La production visée (jitter µs, tick→trade ≤ 1 ms) est **en dessous** de la
zone calibrée gagnante — la contrainte temporelle est confortable ; le
goulot réel est la concurrence sur capture du gap, pas sa survie.

## 5. Instrumentation (`report_insample.py`)

`exec_seq` retourne `{res, why, pnl, fills, parts, dur}` — le replay
détaillé agrège : histogramme de fills par chemin, raisons d'abort, jambes
partielles, durées par nombre de jambes, marges par paire terminale.
