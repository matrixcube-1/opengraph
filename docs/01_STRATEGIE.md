# 01 — Stratégie : arbitrage à chemins ouverts

## 1. L'idée

Sur un marché multi-devises coté par un seul broker, les taux croisés implicites
(le produit des cotations le long d'une chaîne de conversions) s'écartent
transitoirement de leur parité. Une opportunité existe quand **traverser une
chaîne de conversions livre plus de valeur en devise terminale que le notionnel
converti** — nets de commissions et de spreads réels.

Nous ne cherchons **jamais de cycle fermé** (triangle A→B→C→A) :

- les triangles sont la cible la plus saturée de l'arbitrage FX — la
  concurrence y est maximale, les gaps sont capturés en microsecondes ;
- la position nette d'un cycle fermé est nulle → pas d'exposition mais pas de
  levier sur la persistance ;
- un **chemin ouvert** termine sur une devise (ou, plus tard, un instrument
  terminal) — le résiduel porte l'exposition et c'est lui qui capte la
  convergence.

## 2. Le graphe

```
                        USD (nœud 0)
                       /  |  \  ...
                      /   |   \
                  EUR(1) GBP(2) JPY(3) CHF(4) CAD(5) AUD(6) NZD(7)

   8 devises = nœuds · 28 paires = arêtes BIDIRECTIONNELLES
   (chaque paire s'exécute dans les deux sens : base→quote au bid,
    quote→base à 1/ask)
```

**Annexe J.2 — les 28 arêtes** (ordre de référence, ne pas réordonner) :

```
EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD
EURGBP EURJPY EURCHF EURAUD EURCAD EURNZD
GBPJPY GBPCHF GBPAUD GBPCAD GBPNZD
AUDJPY AUDCHF AUDCAD AUDNZD
CADJPY CADCHF CHFJPY
NZDJPY NZDCAD NZDCHF
```

> `CURR = [USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD]` — l'ordre des indices
> (CHF=4, CAD=5, AUD=6) est non-alphabétique ; il est reproduit fidèlement
> dans `engine/src/universe.rs` car toutes les tables en dépendent.

## 3. Énumération des chemins — 2 072

DFS depuis chacun des 8 nœuds, arêtes bidirectionnelles, sans revisiter un
nœud, profondeur ≤ 3 jambes ; **chaque préfixe compte** (chemins de 1, 2 et
3 jambes) :

```
                    chemins ouverts
        1 jambe :   8 départs × arêtes incidentes .............
        2 jambes :  chemins à 2 conversions ...................
        3 jambes :  chemins à 3 conversions ...................
        TOTAL = 2 072 chemins (verrou de test Rust + Python)
```

Exemple — un chemin 2 jambes partant du **côté quote** de sa première arête :

```
départ réel : CAD (p_start = CAD, pas la base du symbole !)
   leg 1 : CADJPY  CAD → JPY   (sens quote→base : × 1/ask — vente de JPY
                                contre CAD en pratique : achat CADJPY)
   leg 2 : EURJPY  JPY → EUR   (sens quote→base : × 1/ask)
   terminus : EUR → marqué qty_EUR × up[EUR]
```

`p_start` enregistre le nœud de départ **réel** : un chemin peut partir du
côté quote de sa première arête — le correctif R6 (directions fantômes) a
verrouillé ce point.

## 4. Formules — le DDD exécutable

À un instant `t`, pour le chemin `p = (e1, e2, e3)` partant de `s` :

### 4.1 État de cotation (forward-fill, fraîcheur bilatérale)

```
ok(sym)  ⟺  last_t(sym) ≤ t   ET   t − last_t(sym) ≤ fresh_ms
```

Toute jambe dont la quote est absente ou trop vieille invalide le chemin
(`net = −inf`). Jamais de quote future (R7 : `ts[j] ≤ t` strict).

### 4.2 Conversion USD de chaque nœud (mid courant)

```
up[USD] = 1
up[c]   = mid(c/USD)      si la paire cUSD existe
up[c]   = 1 / mid(USD/c)  si la paire USDc existe
```

### 4.3 Taux dirigé par jambe

```
rate(e) = bid(e)      si le nœud courant = base de e  (vente base→quote)
rate(e) = 1 / ask(e)  si le nœud courant = quote de e (achat quote→base)
```

### 4.4 Chaîne de valeur

```
qty₀      = NOTIONAL / up[start]                    (notional 100 000 $)
qty_end   = qty₀ × Π rate(e_k)
usd_out   = qty_end × up[end] × ratio_bout          (FX pur : ratio = 1)
comm      = Σ_jambe COMM / up[base(e)]              (≈ 3 $/jambe/100 K base)
net(p,t)  = usd_out − NOTIONAL − comm×comm_scale − P_CB
```

`net > ddd_min` → envoi exécutable. `P_CB` (commission bout) et `ratio_bout`
n'interviennent que sur terminaisons CFD — hors périmètre sous Convention A.

### 4.5 Sélection

```
best_net(t) = max_p net(p,t)   sur les chemins FX (passe 2 bouts désactivée
                               sous Convention A — 0/1 029 811 argmax)
```

## 5. Épisodes et persistance

Un **épisode** = fenêtre contiguë où `best_net(t) > 0` pendant le scan
(évaluation à chaque tick modifié > `move_gate` × spread médian, ou toutes
les 500 ms). Mesurés sur le graphe complet :

```
médiane 296 ms · p90 7,25 s · p99 29,0 s · max légitime 335 s
```

La persistance alimente le modèle Hawkes (MLE dédié, hors Optuna) :
`S(h) = P(épisode survit encore après h)` — décide si le gap survivra à la
séquence d'exécution.

## 6. Terminaisons CFD (phase ultérieure — documenté, désactivé)

L'énumération produit aussi **8 288 variantes bout** (préfixe FX + arête
devise→instrument terminal, 32 bouts Annexe J.3). Sous Convention A :

```
ratio_bout = bid(bout)/ask(bout) < 1   →   tout chemin bout est strictement
                                           dominé par son jumeau FX
```

Conséquence mécanique vérifiée : **0 argmax bout sur 1 029 811 candidats** —
la pureté FX du calibrage est garantie par construction. Leur activation
ultérieure exige une convention de marquage différente (juste valeur
synthétique ou convergence), décision gelée jusqu'à validation production FX.

## 7. Ce que la stratégie capture — et ne capture pas

| Capturé | Non capturé |
|---|---|
| mispricing de taux croisés entre paires du même broker | latence inter-venue (tout est intra-IC Markets) |
| écart persistant ≥ durée d'exécution (~3-8 ms) | gaps micro-seconde (inaccessibles au jitter µs→ms) |
| marges réelles après spreads + commissions réelles | P&L de compte (pas de contrainte de capital/inventaire au POC) |
| chemins 1-3 jambes (92 % des trades à 1 jambe) | terminaisons CFD (dominées sous Convention A) |
