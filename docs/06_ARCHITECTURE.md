# 06 — Architecture : POC Python → cœur Rust → NautilusTrader → cTrader

## 1. Vue en couches

```
┌─────────────────────────────────────────────────────────────────┐
│ FAIT — référence comportementale                                │
│                                                                 │
│   Dukascopy .bi5 ─► fetch_poc.py ─► CSV horaires /opt/arb/data │
│                                        │                        │
│                          poc_engine.py (moteur R7)              │
│                          ├─ quote state rembobinable            │
│                          ├─ eval_at  : DDD vectorisé            │
│                          ├─ exec_seq : fills E1-E9 aux vrais tks│
│                          └─ scan     : candidats + épisodes     │
│                                        │                        │
│                     poc_optuna.py : replay + Optuna v2          │
│                     report_insample.py : replay instrumenté     │
└─────────────────────────────────────────────────────────────────┘
                               │ spec de portage
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ EN COURS — engine/ (Rust, cible production)                     │
│                                                                 │
│   universe.rs  tables J réelles (28 paires, CURR fidèle)  [réel]│
│   paths.rs     enum_paths() + tables précompilées        [réel] │
│   quote.rs     Quote {ts_ms, bid, ask, vol}              [réel] │
│   fees.rs      COMM · NOTIONAL · ABORT_COST             [réel]  │
│   engine.rs    état rembobinable, jamais de futur        [réel] │
│   eval.rs      DDD bid/ask + fraîcheur + spread_cap      [réel] │
│   exec.rs      E1-E9, IOC, partiels, garde volume        [réel] │
│   data.rs      loader CSV /opt/arb/data                  [réel] │
│   scan.rs      scan événementiel + replay (cooldown)     [réel] │
│                                                                 │
│   Parité Python↔Rust vérifiée sur fixture : nets identiques     │
│   à la précision f64 (1 exec à 1,5e-11, contraction FMA).       │
└─────────────────────────────────────────────────────────────────┘
                               │ cœur de calcul partagé
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ FAIT — pile NautilusTrader (audit/nt_realism.py)                │
│                                                                 │
│   CandRealism(Strategy) : on_quote_tick → eval_at → porte ddd    │
│     → _exec_internal (exec_seq Rust) ou _exec_real (LIMIT_IOC    │
│     séquentiels, LatencyModel ns, bouclage exec_settle Rust)     │
│   Paliers mesurés : IOC, latence 0-50 ms, vol_guard, file L1     │
│   (h = spr·(hq_sp + kq·frac)) — 0 flip apparié ; perf §7         │
│                                                                 │
│   regime_indicators/ : boucle lente T̃/D/c⋆ + porte hystérésis   │
│     + modulation gπ — publié ArcSwap, en aval de l'oracle        │
│                                                                 │
│ FAIT — adapter cTrader (ctrader_client, client Rust autonome)   │
│ À FAIRE — couche sizing HiGHS + feu vert externe (API, NETTED)  │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Cœur Rust — porté et vérifié

Le cœur de calcul est **porté intégralement** (étape 3) : état de quotes
rembobinable, `eval_at`, `exec_seq` E1-E9 + garde volume, loader CSV,
scan événementiel et replay avec cooldown. **22 tests unitaires** :
verrou 2 072, pas-de-futur (T1), rembobinage (T4), fraîcheur bilatérale
(T3), rejouabilité inter-trials (T6), portes IOC, partiels, garde volume.

**Parité Python↔Rust** (`audit/gen_fixture.py` + `parity_ref.py` +
`examples/parity.rs`) : même fixture CSV → nets identiques à la précision
f64 sur tous les points d'évaluation, execs identiques (une différence de
1,5×10⁻¹¹ sur un pnl — contraction FMA du compilateur, immatérielle).
Revérifiée **à travers le binding pyo3** (`audit/parity_binding.py`,
module `opengraph` construit `--release` via maturin) : même résultat,
`rel_tol = 1e-9`.

⚠️ **Base de temps des harnais** : le loader Rust et `epoch_ms` parlent
UTC civil ; `time.mktime` donne la base *locale* de la machine (−1 h ici,
selon fuseau/DST). Tout script alimentant le moteur Rust/binding doit
calculer ses epochs en UTC (`calendar.timegm` ou équivalent) — sinon les
evals voient un état décalé d'une heure sans erreur explicite. **Exception
par design** : `on_tick` de la Strategy prend `tick.ts_event // 1_000_000`
(ns→ms direct de Nautilus) — aucun epoch n'est recalculé.

**Ticks à ts égal — deux sémantiques vérifiées** : (a) chaque tick reçu
déclenche une évaluation (`on_tick` par tick), comme le scan POC qui itère
par événement et non par timestamp distinct — le nombre de signaux sera
comparé tel quel sur cand.npz ; (b) la médiane de spread ingérée compte
chaque tick reçu, miroir exact du préchargé où les entrées à ts égal
existent toutes dans la série.

## 3. Spécification manquante assumée — garde volume

Le POC Python lit `bid_vol`/`ask_vol` mais n'en borne pas le fill. Le cœur
Rust devra :

```
fill_qty = min(qty_needed, vol_at_quote_in_base_units)
if fill_qty / qty_needed < q_min:  rejet / abort
qty *= fill_qty / qty_needed
```

avec la conversion d'unités par classe (FX : millions de base ; CFD :
contrats × multiplicateur — à définir par `BOUTS`). Point bloquant avant
toute évaluation CFD ; bénin sur FX à 100 K$ mais **testé quand même**.

## 4. Décisions de parité étapes 4-5 (avant comparaison cand.npz)

Trois points tranchés avant de rejouer les candidats entre les deux moteurs
— sinon on chasse un faux écart :

1. **Mode de backtest déterministe d'abord.** NautilusTrader rejoue en
   événementiel : l'ordre des ticks à timestamp égal entre paires et la
   latence simulée peuvent diverger de `exec_seq`. On fige un mode
   **latence nulle + fill déterministe** pour la parité ; le réalisme
   (latence, files d'attente) est testé dans un second temps.
2. **Deux réserves du rapport in-sample — converties en garanties dures.**
   - Le `replay` Python ré-évalue l'univers complet après `IDX_FX`
     (2ᵉ `eval_at` incluant les bouts). Sous Convention A c'est un no-op
     vérifié (0 argmax bout / 1 029 811) — désormais **assert bloquant**
     dans `poc_optuna.replay` : `ev2 is None or ev2[0] <= ev[0]`. Si un
     chemin bout gagne un jour, la Convention A est remise en cause et le
     replay s'arrête — signal fort, pas divergence silencieuse.
   - `cands` trié par date : vrai par construction (le scan append dans
     l'ordre des événements) — désormais **assert à l'import** :
     `np.all(np.diff(ts) >= 0)` dans `load_cands`, échec bloquant.
   - Ordre à ts égal : verrouillé des deux côtés.
     - Rust : test `ev_order_at_equal_timestamps_follows_fx_table` —
       événements triés stable en **ordre de table FX** (pas alphabétique),
       identique à `np.argsort` stable.
     - NautilusTrader **1.220.0** : vérifié empiriquement
       (`audit/test_nt_order.py`) — le `BacktestEngine` trie stable par
       `ts_init` puis **suit l'ordre d'appel à `add_data`**. Ticks à
       ts identique ajoutés dans l'ordre FX arrivent dans l'ordre FX
       (EUR/USD→CAD/JPY→NZD/CHF, pas alphabétique) ; à ordre d'ajout
       inverse, l'ordre observé s'inverse. **Contrat : alimenter les
       données groupées par instrument dans l'ordre de la table FX.**
       Caveat : comportement propre à la version épinglée — à re-vérifier
       à chaque bump de `nautilus_trader`.
3. **Drapeau `vol_guard`** (`ExecParams.vol_guard` / `Params.vol_guard`) :
   `false` = parité stricte POC (fills non plafonnés) ; `true` = spec Rust
   production. Les comparaisons cand.npz tournent avec `false`.

Plus : comparaison systématique à tolérance explicite `rel_tol = 1e-9`
(`audit/parity_cmp.py`) — les contractions FMA varient selon la cible de
compilation ; une égalité stricte casserait sans raison.

## 5. Gates de performance bloquants (runbook §5-6)

| Gate | Seuil | Mesure |
|---|---|---|
| Calcul chemin + DDD | **p99 < 1 ms** | histogramme interne, ticks réels |
| End-to-end Strategy→ordre | **p99 < 5 ms** | horodatage entree/sortie adaptateur |
| Débit backtest | **≥ 10 000 ticks/s** | rejeu jours réels |
| Mémoire | stable, pas de fuite | soaks longue durée |

Tout manqué = retour conception, pas d'assouplissement. Traces archivées
pour le rapport PoC.

## 6. Séquence runbook (état)

```
[1] Object Storage Vultr ......... décalé (snapshot « Opengrah » tient lieu
                                  de backup ; bucket requis pour artefacts
                                  d'audit + dataset 20 mois)
[2] GitHub + squelette Rust ...... FAIT — push initial, enum_paths verrou
[3] Cœur FX Rust Convention A .... FAIT — port complet + 22 tests +
                                  parité f64 vs POC (1,5e-11 max, FMA)
[4] Strategy NautilusTrader ...... FAIT — PARITÉ NT↔POC validée serveur :
                                  62 145 comparaisons, 0 divergence —
                                  51 767 evals + 10 378 execs identiques
                                  (t_free + ddd_min au même endroit que
                                  replay(), latency_model=None,
                                  exec_seq interne, vol_guard=false).
                                  NB population : 46 731 → 10 378 execs
                                  = verrous t_free + porte ddd_min de
                                  replay() (les ~36 k candidats à net<0
                                  du scan généreux sont filtrés) — PAS
                                  une perte de signaux : même règle des
                                  deux côtés, comptes identiques.
                                  NT 1.230.0 (1.220.0 sans wheel cp314) —
                                  ordre à ts égal re-vérifié sur 1.230.0.
                                  57 086 ticks/s mesurés (gate ≥10 k OK).
                                  Étape 3 confirmée : binding pyo3
                                  0.28.3, parité cand.npz 51 816/0 div.
[5] Backtest interne ............. EN COURS — paliers de réalisme, voir §6.1
[6] Gates latence/perf ........... bloquants
[7] Holdout jour 26 .............. UNIQUE, sur pile NT finale
[8] Rapport PoC FX ............... consolide tout
[9] Adapter cTrader + push final . après validation
```

## 6.1 Étape 5 — réalisme en paliers (`audit/nt_realism.py`)

Harnais : même Strategy NT, deux modes — `internal` (référence `exec_seq`,
zéro venue) et `real` (vrais `LIMIT_IOC` sur la venue `SIM`, bouclage Rust
`exec_settle` sur les fills réels). Échantillon `step 20 + 3 jambes` =
51 802 candidats, 3 jours (22, 23, 26 oct. 2026), ~10,4 k exécutions par run.
Différentiel candidat-par-candidat via `audit/diff_runs.py` — chaque écart
attribué à UN réglage.

**Sémantique venue mesurée (NT 1.230.0, empirique)**
- IOC : fill plafonné à la taille affichée L1, reliquat CANCELED
- `LatencyModel` en **nanosecondes** ; latence quantifiée aux événements :
  l'ordre est traité au premier tick ≥ t+lat, contre SON livre
- Fill au prix L1 courant (amélioration de prix possible dans la limite)
- `exec_settle` Rust boucle les fills réels et rend `(pnl, filled, parts,
  resid_usd@t0)` ; résiduel marqué fin de run via `usd_marks` — réconcilié
  au dollar près : `resid_end = resid_t0 + drift`

**Limite IOC = send ± 3·tau_slip** — une limite à la touche annulait sur le
moindre mouvement adverse (351 kills artificiels à 1 ms). La bande ±3·tau
(2,4 bp) reproduit la porte kill d'`exec_seq` et le comportement réel d'un
IOC protégé — après fix : **0 flip à tous les paliers**.

**`parts` ≠ cap L1** : les ~4 000 execs avec `parts>0` sont des jambes buy
à `frac_eff = cq·px/acc < 1` (arrondi de quantité + amélioration de prix),
pas des partiels de liquidité — mesuré : **0 cap L1 réel sur 11 520 jambes**
(le côté affiché ≥ besoin à tous nos timestamps).

**Garde volume lue par côté (fix Rust)** : `vol_guard` lisait `max(av,bv)`
— sur livre déséquilibré elle regardait le mauvais côté. Corrigé :
`avol`/`bvol` stockés, la garde lit le côté frappé (TDD :
`exec_volume_guard_reads_hit_side_not_max`). Effet chiffré sur cette
population : nul (0 cap même côté-correct) — fix de spec.

**Modèle file/slippage (palier D)** — approx. documentée L1-only : le fill
reste plafonné au L1 affiché (pas de fill fictif L2) ; le prix effectif est
dégradé `px_eff = px·(1−d·h)`, `h = spr·(hq_sp + k_q·min(1, qty/disp))` —
coût monotone dans la fraction du L1 consommée, en unités de spread.
A priori `hq_sp=0,1` (file invisible), `k_q∈{0,5 ; 1,0}` ; grille 2×3.

### Résultats (population step-20, 51 802 cands)

| Palier | Config | Execs | PnL | Δ vs réf |
|---|---|---|---|---|
| R0 réf | internal lat0 | 10 413 | +63 429,17 | — |
| A IOC | real lat0 | 10 413 | +63 177,09 | −252 (−0,40 %) |
| B vol_guard | internal vg | 10 413 | +63 429,17 | +0,00 (garde inerte à ~100 k$) |
| C 1 ms | int / real | 10 400 / 10 362 | +63 121 / +62 298 | −823 (−1,3 %) |
| C 5 ms | int / real | 10 366 / 10 344 | +62 338 / +61 780 | −558 (−0,9 %) |
| C 20 ms | int / real | 10 323 / 10 315 | +60 859 / +60 428 | −431 (−0,7 %) |
| C 50 ms | int / real | 10 300 / 10 297 | +59 011 / +58 766 | −245 (−0,4 %) |

### Grille D — file/slippage à lat 5 ms (vs real sans fila +61 780)

`px_eff = px·(1−d·h)`, `h = spr·(hq_sp + k_q·min(1, qty/disp))` — paramètres
en unités de SPREAD, fixés a priori (`hq_sp` = file invisible / sélection
adverse non observable ; `k_q` = impact par fraction du L1 consommé).

| hq_sp | k_q | Execs | PnL | Δ vs sans fila |
|---|---|---|---|---|
| — | — | 10 344 | +61 780 | — |
| 0,1 | 0 | 10 344 | +50 807 | −17,8 % |
| 0,1 | 0,5 | 10 344 | +46 807 | −24,2 % |
| **0,1** | **1,0** | 10 344 | **+42 806** | **−30,7 %** |
| 0 | 0,5 | 10 443 | +58 366 | −5,5 % |
| 0 | 1,0 | 11 148 | +58 416 | −5,4 % |

Notes grille : `caps=0` partout (L1 jamais limitant). Les configs hq=0
produisaient des execs en plus (+99/+804) — attribués d'abord à un effet
d'agenda de 2ᵉ ordre, **la vraie cause était la pollution de données décrite
ci-dessous** (fichiers CSV réécrits pendant les runs). Les comptes d'execs
des runs du 30/10 ≥13 h sont à traiter comme suspects ; les écarts appariés
restent valides. Re-validation propre en cours sur données gelées.

Lecture : la latence coûte ~7 % de PnL interne à 50 ms (63 429 → 59 011) ;
l'écart real-vs-internal se RESSERRE avec la latence (les fills dans la bande
±3·tau compensent partiellement) — la décision de colocation dépend surtout
du coût interne de latence, pas de l'écart venue. **Le modèle de file est le
réducteur dominant** (−30 % au pire plausible, stratégie toujours bien
positive) — le chiffre qui comptera au jour 26.

### Étape 5.6 — incident de pollution de données (30/10, audit trail)

Un processus `fetch_poc.py` lancé le 27/10 restait actif : coincé dans le
tarpit WAF, il rebouclait ses repasses et **réécrivait les CSV des 3 jours
POC** (317 fichiers entre 13 h et ~19 h UTC). Un run lisant un fichier à
mi-écriture voyait une heure tronquée → état moteur divergent → argmax
d'eval différent au même `t` (preuve : GBPUSD/−3.73 vs AUDJPY/−2.66 à
t=1792724877113, comptes de ticks différant de 17 104). Processus tué ;
200 fichiers sondés sains après stabilisation. Conséquences :

- valides : tous les écarts **appariés** (0 flip, PnL bit-identique), les
  mesures temporelles (ns), les runs antérieurs au 30/10 13 h ;
- suspects : exec counts et PnL globaux des runs du 30/10 ≥13 h, incluant
  les anomalies d'execs de la grille D ;
- action : re-validation des chiffres clés sur données gelées (ladder7).

**Règle ajoutée** : un seul downloader à la fois — et aucun run d'analyse
pendant qu'un fetcher écrit dans `/opt/arb/data`.

## 7. Étape 6 — gates de performance (30/10)

Mesures `perf_counter_ns` dans le process (`nt_realism.py --perf`), mode
real IOC + file (0,1 sp × 1,0 sp) à lat 5 ms, population step-20 :

| Gate | Cible | Mesuré | Statut |
|---|---|---|---|
| p99 calcul `eval_at` | < 1 ms | **61-62 µs** (max 352 µs gelé) | ✓ ×16 |
| p99 calcul `exec_seq` | < 1 ms | **20 µs** | ✓ |
| p99 e2e tick→`submit_order` | < 5 ms | **536-544 µs** | ✓ ×9 |
| max e2e | — | 512 ms brut → **1,3 ms avec `gc.freeze()`** | ✓ |
| débit scan (étape 4) | ≥ 10 k t/s | **57 086 t/s** | ✓ |
| débit venue+ordres | — | 9,0-9,3 k t/s | info backtest |
| mémoire (3 j, real) | stable | 3 186 → 3 364 Mo plafonné | ✓ |

`gc.freeze()` après chargement des 10,5 M ticks supprime les pauses GC :
max e2e 512 ms → 1,3 ms, max calc 21,6 ms → 0,35 ms. La queue RSS (+180 Mo)
est l'accumulation voulue des positions résiduelles Convention A, pas une
fuite. À figer en prod : `gc.freeze()` après `add_data`, avant `run()`.

## 8. Invariants non négociables (audit trail)

1. **Jamais de quote future** — `last_t ≤ t` vérifiable à chaque lecture.
2. **Fraîcheur bilatérale** — `t − last_t ≤ fresh_ms` ET pas de futur.
3. **Exécution séquentielle** — fills aux vrais ticks ultérieurs, jamais
   atomiques, jamais à la quote de détection.
4. **Convention A** — résiduels marqués aux prix exécutables.
5. **Holdout unique** — jour 27/10 : évalué une fois en aveugle
   (`ddd_min = 0`, −60,65 $), puis une fois en post-hoc documenté
   (`ddd_min = 2,5`, +11,25 $) — désormais non aveugle pour cette famille.
6. **Pas de cycle fermé** — chemins ouverts uniquement, `assert` structurel.
7. **`comm_scale ≥ 1.0`** — jamais sous le taux négocié.
8. **Un downloader à la fois** — discipline WAF documentée (02_DONNEES.md).
9. **Indicateurs de régime en aval de l'oracle** — jamais de pruning /
   réordonnancement des chemins ; fail-closed si données périmées.

## 9. Couche régime + audit du seuil (31/10)

**`regime_indicators/`** — crate sœur (43 tests), boucle lente hors chemin
critique (~3 µs/pas) : tension multi-sauts T̃ (résidu de Hodge, Ā+A²+A³,
Frobenius, EWMA+MAD), dominance spectrale D/c⋆, porte à hystérésis
fail-closed, modulation bornée `g_π`, instantané `ArcSwap`. Formules et
mesures : [07_REGIME_SEUIL.md](07_REGIME_SEUIL.md).

**Audit du seuil — constat majeur** : ni le replay Python ni `exec_seq`
interne ne reproduisent les jours perdants (22/10 +1,2 M$ vs venue +48 k$) —
le saignement vit dans la couche venue (caps L1, haircut de file, IOC au
tick ≥ t+lat). Le seuil a donc été calibré par **re-sérialisation exacte
du flux venue** (`poc_thresh_grid.py`, validé au centime sur le baseline) →
plateau `ddd_min ∈ [2, 4]` → retenu **2,5 $**, `cooldown_s = 0`.

Résultats : in-sample 3 jours tous positifs (+49,6 k$ total vs 47,7 k$) ;
holdout 27/10 post-hoc **+11,25 $ / 6 execs** vs −60,65 / 134 execs
(prédiction resim exacte au centime). Détail complet :
`audit/HOLDOUT_2026-10-27.md` addendum.

## 10. Reste à faire (production)

- Adapter venue cTrader — **fait** comme client Rust autonome
  (`ctrader_client/`, pas via traits DataClient/ExecutionClient NT) :
  protobuf/TCP-TLS, auth 3 couches, garde NETTED fail-closed, spots natifs,
  exécution IOC séquentielle, reconcile ; 60 tests + auth réelle validée +
  pipeline mock E2E. En attente : approbation app OpenAPI + compte NETTED.
- Couche sizing HiGHS/Kelly/ES — nécessite `μ`/`σ²` par chemin non encore
  spécifiés ; `g_π` prêt en amont (§7 du doc de conception).
- Hawkes MLE / `p_persist` — sémantique à finaliser, épisodes exportés.
- Porte à hystérésis : implémentée, non activée sur le déclenchement
  (coût d'opportunité mesuré) — disponible en overlay de risque.

## 10.1 Séquence de câblage — quand et pourquoi pas maintenant

```
MAINTENANT   → feu vert externe : approbation app OpenAPI + compte NETTED
             → detect-only natif sur ctrader_client (shadow live)
PUIS         → Hawkes MLE (stats offline, épisodes déjà exportés)
             → porte hystérésis en overlay risque / kill-switch
             → collecte μ/σ par chemin sur flux réel
ENSUITE      → concurrence multi-chemins → HiGHS devient pertinent
```

- **Porte à hystérésis** : câblage technique trivial (crate testée,
  ArcSwap) ; blocage *stratégique* — elle ferme sur haute tension =
  jours de récolte. Rôle : overlay de risque, pas gate de trading.
- **Hawkes / `p_persist`** : auto-contenu (estimateur MLE offline, zéro
  changement moteur) ; sémantique événement/résorption à finaliser (M4).
- **HiGHS / Kelly / ES** : blocages de *conception*, pas de code —
  (a) estimation `μ_USD`/`σ²_USD` par chemin non spécifiée (μ<0 → LP
  infaisable ; σ²→0 → explosion ; 2 072 chemins épars → pooling
  hiérarchique par jambes requis) ; (b) `t_free` sérialise : une seule
  position à la fois → le sizing multi-chemins n'a rien à allouer tant
  que la concurrence n'est pas autorisée. `g_π` s'appliquera sur les
  seconds membres du LP quand la couche existera.
