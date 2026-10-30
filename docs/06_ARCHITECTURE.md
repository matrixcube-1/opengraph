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
│ À FAIRE — intégration production                                │
│                                                                 │
│   NautilusTrader Strategy (pyo3/natif)                          │
│     on_quote_tick → sync état → eval DDD → règle d'envoi        │
│     → ordres IOC séquentiels au venue adapter                    │
│                                                                 │
│   cTrader venue adapter (DataClient / ExecutionClient custom)   │
│     - abonnements ticks 28 paires                                │
│     - mass status + probes IOC                                   │
│     - la Strategy ne touche JAMAIS l'API venue                  │
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
evals voient un état décalé d'une heure sans erreur explicite.

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
[4] Strategy NautilusTrader ...... EN COURS — ordre à ts égal vérifié
                                  (NT 1.220.0 : stable ts_init, suit
                                  l'ordre d'ajout des données = table FX)
                                  + binding pyo3 validé : un appel/tick
                                  (on_tick ingère+évalue, signal ou None),
                                  GIL relâché, parité f64 re-vérifiée
                                  à travers le module --release
[5] Backtest interne ............. à faire
[6] Gates latence/perf ........... bloquants
[7] Holdout jour 26 .............. UNIQUE, sur pile NT finale
[8] Rapport PoC FX ............... consolide tout
[9] Adapter cTrader + push final . après validation
```

## 7. Invariants non négociables (audit trail)

1. **Jamais de quote future** — `last_t ≤ t` vérifiable à chaque lecture.
2. **Fraîcheur bilatérale** — `t − last_t ≤ fresh_ms` ET pas de futur.
3. **Exécution séquentielle** — fills aux vrais ticks ultérieurs, jamais
   atomiques, jamais à la quote de détection.
4. **Convention A** — résiduels marqués aux prix exécutables.
5. **Holdout une fois** — jour 26 exposé une seule fois, sur la pile finale.
6. **Pas de cycle fermé** — chemins ouverts uniquement, `assert` structurel.
7. **`comm_scale ≥ 1.0`** — jamais sous le taux négocié.
8. **Un downloader à la fois** — discipline WAF documentée (02_DONNEES.md).
