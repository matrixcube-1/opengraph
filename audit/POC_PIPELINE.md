# Pipeline POC — Arbitrage FX open-path (3 jours de ticks)

Serveur : `root@${ARB_HOST}` (Ubuntu 26.10, 8 cœurs, 15,7 Go) · code `/opt/arb/poc` ·
données `/opt/arb/data`. Sources : `audit/fetch_poc.py`, `poc_engine.py`,
`poc_optuna.py`, `poc_report.py` (réplicas du dossier `audit/` du repo).

**Règle d'or** : 100 % ticks réels Dukascopy. Aucun Monte Carlo, aucune donnée
synthétique dans la chaîne. `mc_sim.py` est un artefact antérieur hors pipeline.

---

## Vue d'ensemble

```
ÉTAPE 1  ACQUISITION          fetch_poc.py  -> .bi5 LZMA -> CSV/heure
ÉTAPE 2  DÉTECTION (backtest) poc_engine.Engine.scan -> cand.npz
ÉTAPE 3  OPTIMISATION         poc_optuna.py run -> Optuna v5 sur E1-E9
ÉTAPE 4  VALIDATION           holdout (1 fois) + importance + poc_report
ÉTAPE 5  SUITE (hors POC)     Hawkes MLE dédié + campagne 20 mois
```

---

## ÉTAPE 1 — Acquisition `fetch_poc.py`

| Aspect | Implémentation |
|---|---|
| Source | `datafeed.dukascopy.com/.../{h}h_ticks.bi5` (mois URL 0-indexé) |
| Format | LZMA -> records `>iiiff` (ms_off, ask, bid, ask_vol, bid_vol) |
| Univers | **exactement Annexe J** : 28 paires FX puis 32 CFD triés (J.4 exclus) |
| Ordre | `FX_FIRST` : EURUSD→GBPUSD→USDJPY→…→NZDCHF, puis bouts A→Z |
| Rythme | 1 connexion globale, ~1 req/s (tarpit amont ~850 o/s, pas de parallélisme) |
| Erreurs | 503/URLError -> backoff 15/45/120 s · 403/429 -> pause 2 h · 6 passes de requeue |
| Sortie | `/opt/arb/data/{SYM}/{AAAA-MM-JJ}/{HH}.csv` + `poc_ledger.json` idempotent |
| Qualité | diviseur auto par paire (score vs niveau de référence), `bid<=ask`, ms croissants, comptage ticks |
| Empreinte | 0 % CPU, ~33 Mo RAM — I/O-bound, ne concurrence jamais Optuna |

## ÉTAPE 2 — Détection `poc_engine.py` (backtest événementiel)

**Chemins** — énumération structurelle constante :

| Chemins | Décompte | Règle |
|---|---|---|
| FX seuls | 56 + 336 + 1 680 = **2 072** | `MAX_LEGS=3`, paires bidirectionnelles, nœuds sans répétition |
| + terminaison bout | **8 288** | puits cotés dans la devise finale (USD 16, EUR 7, AUD 3, GBP/JPY/CHF 2, CAD/NZD 0) |
| **Total** | **10 360** | split `P_HASB` **structurel** — indépendant de l'état de chargement |

**État** : forward-fill par instrument (`tick`/`sync`, O(1) par événement), quotes `bid/ask`
réelles + `last_t` (âge). Chemins invalidés proprement quand données manquantes
(`net = -inf`) — jamais de fantôme ni de KeyError.

**`eval_at(t, fresh_ms, spread_cap, comm_scale)`** — vectorisé numpy :
- arêtes fraîches (`t - last_t <= fresh_ms`) + spread FX <= cap × médian réel
- taux exécutables : vente base au `bid`, achat base au `1/ask` (convention B)
- commissions USD : `3,50 / P_base→USD` par jambe (formule §8.7) × `comm_scale`
- unifié bout : `usd_out = qty_end × (bid/ask)_duka × up[nœud_IC]` — le facteur de
  conversion s'annule dans le ratio (résidu : CONV_COST SWI20 ≈ 4 USD)
- **2 passes** : `IDX_FX` (2 072) à chaque instant ; `IDX_BT` seulement si préfixe
  candidat — une terminaison ne peut pas battre son préfixe au spread près

**`exec_seq(t0, pi, lat_ms, tau_slip_bp, q_min)`** — rejeu séquentiel E1–E9 :
- fill au dernier tick `<= t + lat_ms` par jambe (quote réelle, pas la détection)
- kill IOC si dérive > 3×τ_slip · fill partiel 60 % si dérive ∈ ]τ_slip, 3τ_slip]
- `frac < q_min` -> abort ; abort -> `_abort` (perte ~12 USD/jambe remplie + cash)
- cash résiduel par jambe partielle, marquage final USD via `up[node]`
- jambe bout : quote fraîche <=500 ms exigée, bout non chargé -> `None` (jamais préfixe)

**`scan(...)`** — 1 passe : évalue seulement quand le mid de la paire tickée a bougé
> `move_gate` × spread médian (ou toutes les `eval_every_ms`) -> `cand.npz`
(candidats `(t, pi, net, ddd)` + épisodes `(t0, t1, pi, net_max)` — input Hawkes).

Scan générateur = sur-ensemble : `fresh=2000, cap=3.0, thresh=-50` — les trials
filtrent ensuite *à l'intérieur* de l'enveloppe.

## ÉTAPE 3 — Optimisation `poc_optuna.py` (Optuna v5, inventaire §15-18)

**Objective** = P&L net réalisable cumulé sur les jours in-sample, par rejeu
E1–E9 des candidats réels — détection ré-évaluée sous les params du trial.

**8 paramètres** :

| Groupe | Param | Plage | Rôle |
|---|---|---|---|
| Détection | `fresh_ms` | 100–2000 (log) | âge max des quotes |
| Détection | `spread_cap` | 1.2–3.0 | cap × spread médian par paire |
| Détection | `comm_scale` | 0.8–1.5 | sensibilité coûts |
| Décision | `ddd_min` | 0–40 | seuil net minimal pour tenter l'exécution |
| Exécution | `lat_ms` | 1–10 | latence signal->fill par jambe |
| Exécution | `tau_slip_bp` | 0.3–5 (log) | tolérance dérive IOC |
| Exécution | `q_min` | 0.1–0.95 | fill partiel minimal |
| Exécution | `cooldown_s` | 0–120 | délai post-trade |

**Garde-fous inventaire §16 implémentés** :

| Contrôle | Implémentation |
|---|---|
| Split temporel | jours 22+23 in-sample (steps `trial.report`) ; **jour 26 hold-out, UNE fois** |
| Pruning | `MedianPruner(n_startup=8)` sur P&L cumulé par jour |
| Contraintes hard | `set_constraint` : trades>=10, abort_rate<=30 % |
| Baseline | `enqueue_trial` défauts doc (fresh 500, cap 1.7, lat 2.5, slip 0.8, q 0.5) |
| Anti-biais sampler | study `poc_exec_rand` (RandomSampler) de comparaison |
| Sampler | `TPESampler(multivariate=True, constant_liar)`, seed=20261027, n_jobs=1 |
| Persistance | `sqlite:///opt/arb/poc/optuna.db` + `load_if_exists` |
| Best | `pick_best` : faisables d'abord, sinon meilleur brut (pas de crash v5) |
| Importance | `get_param_importances` (PedAnova) — mode `importance` |
| Artefacts | `best_params.json`, `holdout.json`, `cand.npz`, logs |

**Non câblé (justifié)** : multi-objectif NSGA-II/Pareto (mono-obj + contraintes
suffit au POC), walk-forward multi-folds (impossible sur 3 jours — viendra avec
les 20 mois), heartbeat/gRPC (mono-process), Hub samplers (TPE = bon choix §17).

## ÉTAPE 4 — Validation

- `poc_optuna.py holdout` : best params -> jour 26, **une seule fois** -> `holdout.json`
- `poc_optuna.py importance` : PedAnova
- `poc_report.py` : couverture instrument×jour, ticks, bad_px/bad_seq, trous

## ÉTAPE 5 — Suite (hors POC)

- **Hawkes MLE dédié** sur les épisodes `cand.npz` (λ, S(h) — JAMAIS via Optuna)
- Campagne fenêtre scellée : 2025-01 -> 2026-08, univers 60 complet, walk-forward
- Multi-objectif Pareto (P&L × drawdown), PartialFixed pour le passage prod

## Runbook

```bash
ssh -i ~/.ssh/arb_ed25519 root@${ARB_HOST} ; cd /opt/arb/poc
python3 -u poc_optuna.py scan        # -> cand.npz (une passe, ~10-60 min)
python3 -u poc_optuna.py run 150     # campagne (reprise auto via optuna.db)
python3 -u poc_optuna.py holdout     # jour 26, une fois
python3 -u poc_optuna.py importance
python3 poc_report.py
# monitoring : tail -f fetch.log | sqlite3 optuna.db 'select state,count(*) from trials group by state'
```

## Limites assumées du POC

- 3 jours = **validation mécanique**, pas calibration ni preuve de rentabilité
- 1 fold train + 1 fold hold-out — le vrai anti-overfitting exige les 20 mois
- Hawkes non encore implémenté (épisodes exportés, MLE à venir)
- p_persist non branché au replay (seuil produit complet tranché au doc — la
  calibration S(h)/Ĥ exige la MLE Hawkes d'abord)
