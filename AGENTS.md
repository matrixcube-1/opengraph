# Arbitrage FX — notes projet

## Documents canoniques (état post-RETRAIT R5)
- Méthodologie : chemins ouverts EXCLUSIVEMENT. Jamais de triangles/cycles fermés —
  motif utilisateur : complexité faible → concurrence saturée ; les chemins ouverts
  (mixés CFD plus tard) sont la seule opportunité réelle.
- Les deux PDFs canoniques sont purgés de la machinerie cycles fermés (§6.4,
  Bellman-Ford, DetectCycles, règle #25) — gardés : §1 comparatif + Tableau 1.1
  (bornage), surveillance métal-croisé (QC cotation, jamais chemins), note CME
  « croisement logique » (budget 4 fills, pas du cycle).
- Backups pré-purge : `*.pre_purge.pdf` à la racine. Script : `audit/purge_cycles.py`.
- Extractions txt régénérées : `MC/constitution_unifiee.txt`, `MC/document_scelle.txt`.

## Périmètre des données (ne pas élargir)
- Univers opérationnel (décision 31/10/2026) : 28 paires FX + **26 bouts
  liquides** (13 indices + 11 métaux + 2 énergie) = 54 instruments.
  Les 6 softs — SUGAR, COCOA, COFFEE, COTTON, SOYBEAN, OJUICE — sont
  **définitivement exclus** : faible liquidité, données supprimées du
  serveur, clés ledger retirées, ne pas re-télécharger.
- Univers scellé d'origine = Annexe J du Document scellé : 28 paires FX (J.2) + 32 bouts (J.3) = 60 instruments.
  Exclus (J.4) : WHEAT, CORN, ES35, CHINA50, XNGUSD, HK50, SA40, paires feuilles, CME.
- Conversions J.3 à l'ingestion : SWI20 (CHEIDXCHF) CHF→USD via USDCHF ; *CMDUSX ÷ 100.

## Orientation stratégique (décision utilisateur)
- **Solution A retenue** : déploiement prioritaire sur l'univers FX pur, sous
  **Convention A** (résiduel bout marqué au bid = valeur de liquidation,
  sémantique stricte du document scellé).
- Intégrité du calibrage, reproductibilité, maîtrise opérationnelle avant
  toute extension. L'élargissement CFD = objectif de développement ultérieur,
  conditionné à la validation complète de la chaîne FX sur données réelles via
  les APIs de production + démonstration de robustesse opérationnelle.
- **Conséquence mécanique vérifiée** (cand.npz, 29 oct.) : sous Convention A
  les 8 288 chemins bouts sont *strictement dominés* par leur jumeau FX
  (ratio bid/ask < 1 + commission bout) → 0/1 029 811 candidats argmax sur
  bout. La pureté FX est donc garantie par construction, les bouts ne
  peuvent pas contaminer le calibrage FX.
- Quand les CFD seront activés plus tard, la sémantique du marquage résiduel
  devra être retranchée (A liquidation / B juste valeur synthétique /
  C convergence) — sous A stricte aucune marge bout n'est exprimable.
- **Runbook de référence** : `Runbook_Solution_A_NautilusTrader.pdf` (bureau
  utilisateur, oct. 2026) — séquence : 1) Object Storage Vultr
  (raw/, processed/, holdout/, reports/, artifacts/) ; 2) dépôt GitHub +
  squelette Rust ; 3) cœur calcul FX Rust (Convention A, L≤3) ; 4) Strategy
  NautilusTrader ; 5) backtest interne données téléchargées ; 6) tests perf/
  latence (bloquants : calcul chemin <1 ms p99, end-to-end <5 ms p99,
  ≥10 k ticks/s, mémoire stable) ; 7) holdout jour 26 + PedAnova — sur la
  pile NT, PAS sur le POC Python (in-sample seul) ; 8) rapport PoC FX ;
  9) adapter cTrader custom (traits DataClient/ExecutionClient) + push final.
  Convention A = « marquage aux prix exécutables ».

## Métaux — STAND-BY (décision utilisateur, nov. 2026)
- Sauts métal intérieurs (XAU/XAG) implémentés dans le POC (`mixed=True`, opt-in) mais
  **désactivés** : production = FX-only. `mixed=False` reste le défaut ; ne pas porter en Rust.
- Motif : croisés XAUEUR/JPY/GBP/CHF/AUD, XAGEUR/AUD peu liquides (3-5x moins de ticks,
  quote toutes 400-970 ms vs 100-150 ms XAUUSD/XAGUSD, spreads plus larges) -> prix
  vraisemblablement synthétiques ; edge détecté = retard de quote, pas exécutable.
  Avec XAUUSD/XAGUSD seuls, aucun saut intérieur n'existe (mono-USD).
- Verdict Optuna (holdout 26/10) : mixte #179 +894 $ < FX #87 +955 $.

## Téléchargement Dukascopy (dukascopy-node 1.51.0)
- `-to` est EXCLUSIF : pour un mois complet, `-to` = 1er jour du mois suivant.
- Rythme : un seul processus pour tout le serveur, `-bs 1 -bp 1000` (≈1 req/s).
  Le parallélisme (36–96 requêtes simultanées) a provoqué 429 puis un blocage 403 durable.
- Au premier 403/429/WAF : pause de 2 h (géré par `pull_all.py`), ne pas marteler.
- Le jeton WAF s'obtient via navigateur (Chromium/Chrome) ; un seul lancement, puis cache.

## Scripts (audit/)
- `bootstrap.sh` installation Ubuntu 24.04 · `preflight.sh` tests d'accès avant campagne
- `pull_all.py` campagne (ledger `/opt/arb/data/ledger.json`) · `systemd_install.sh` services
- `verify_coverage.py` contrôle qualité (jours ouvrés manquants, bid≤ask, monotonie), remise en file ≤ 2
- `audit_quality.py` audit multi-facteurs détaillé · `test_pipeline.py` tests hors ligne (Linux)
- `ssh_run.py` : `ARB_HOST` + clé `~/.ssh/arb_ed25519` (pas de mot de passe dans le code)

## POC 3 jours (serveur $ARB_HOST, /opt/arb/poc)
- `fetch_poc.py` : `.bi5` direct Python (pas de dukascopy-node), 3 jours (22/23/26 oct. 2026),
  28 FX d'abord puis 32 bouts ; ledger `/opt/arb/data/poc_ledger.json` ; diviseur auto-détecté ;
  503 → backoff 30/90/300/600 s ; 403/429 → pause 2 h ; timeout 60 s (réponses ~18 s).
- `poc_engine.py` : replay événementiel forward-fill ; par défaut 2 072 chemins FX purs
  (terminaisons bout désactivées : `INCLUDE_TERMINAL_BOUTS=False`, dominées 0/1 029 811
  argmax) ; `mixed=True` opt-in ajoute les sauts métal INTÉRIEURS XAU/XAG
  (1 hop métal = 1 jambe logique, 2 ordres séquentiels, résiduel en devise —
  Convention A) → 8 810 chemins ; `eval_at` vectorisé (IDX_FX d'abord, full si
  candidat) ; `exec_seq` E1–E9 (fill au dernier tick ≤ t_fill, IOC τ_slip, partiel,
  unwind, liquidation résiduel métal à quote fraîche, jamais préfixe FX) ;
  `scan` -> `cand.npz`. Tests sans data : `test_mixed.py` (P_S==p_start, cohérence
  prix, parité FX-only). ATTENTION : `P_S` doit toujours valoir `p_start` — une
  jambe 0 métal le laissait à USD → faux nets ~+10 k (corrigé, test T1).
- `poc_optuna.py` : `scan` | `smoke` | `run N` | `holdout` | `importance`. Inventaire Optuna v5
  appliqué : split temporel (jours 22+23 in-sample = steps MedianPruner, jour 26 hold-out évalué
  UNE fois), `set_constraint` min_trades>=10 + abort_rate<=30 %, `enqueue_trial` défauts du doc,
  study `poc_exec_rand` (RandomSampler) de comparaison, TPE multivarié seed fixe n_jobs=1,
  SQLite `optuna.db` + load_if_exists, PedAnova post-study. Hawkes MLE : HORS Optuna.
- `poc_report.py` : couverture instrument × jour, ticks, anomalies.
- Upstream Dukascopy = tarpit nginx (~850 o/s, 503 à la concurrence) — ne PAS paralléliser.
- SSH : `pkill -f` se suicide sur auto-match -> pattern avec `[.]` ; lancer en fond via
  `setsid bash -c '...' < /dev/null > /dev/null 2>&1` + `disown` (SSH CLI, pas paramiko exec).

## Paramètres opérationnels IC Markets (utilisateur)
- **Latence ECN** : jitter en microsecondes, tick→trade ≤ 1 ms total.
  `lat_ms` du moteur (1-10 ms) est donc pessimiste → marge de sécurité en
  calibrage.
- **Commission** : 6 $ AR/100 K exécuté (3 $/côté — `COMM=3.00` dans poc_engine).
  Paliers de volume : peut descendre jusqu'à 3 $ AR/100 K (1,50 $/côté) — upside
  futur, `comm_scale` planché à 1,0 pour la campagne PoC (ne pas calibrer sur
  un taux non confirmé).
- **Langage cible production : Rust** — le POC Python sert à valider la
  méthodologie, les données et l'économie ; l'engine de production sera
  réimplémenté en Rust (décision post-PoC).
- Cadrage : uniquement ce qu'IC Markets considère acceptable (univers Annexe J,
  PDS S1-S4, pas de CME dans l'implémentation IC).

## Vérification
- `python test_pipeline.py <dossier_src>` sur le serveur (13 vérifications attendues).
- `python test_r7.py` sur le serveur : T1-T6 — quote future, rembobinage pos,
  rejouabilité inter-trials (post-R7).
