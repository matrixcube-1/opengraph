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

## Correction Convention A (audit externe, nov. 2026)
- L'audit a prouvé que le marquage résiduel était au **mid** (`usd_price`),
  pas aux prix exécutables : les +955 $ du holdout étaient un artefact
  comptable. Corrigé partout : `_usd_liq` dans `poc_engine.py`,
  `usd_liq_price`/`usd_liq_comm` dans `engine` (eval, exec_seq, exec_settle,
  usd_marks). La devise terminale est désormais valorisée à la liquidation
  exécutable (bid/ask de sa paire USD) + commission de liquidation.
- Rejeu #87 holdout 26/10 sous Convention A stricte : **+61,58 $ / 12 trades /
  0 abort** (vs +954,76 sous mid) ; fenêtre live : +54,92 / 11 cycles.
  Les paramètres #87 sont indicatifs : la calibration Optuna s'est faite
  sous marquage mid — recalibrage sous Convention A stricte requis avant
  tout mode trade.
- Audit C3 corrigé : `start_qty`/`exec_settle` ne font plus `sync(t0)` en
  live (les bindings pybind font un `sync` explicite pour le replay).
- Audit m1/m2 corrigés : skip `_net0 < ddd_min` supprimé (borne fausse) ;
  médiane de spread = in-sample seul (`MED_CUTOFF`).
- Bloc `ctrader_client` corrigé (voir DEPLOY.md §2 pour le détail) :
  C2 résiduel liquidé immédiatement après `Done` (file d'unwind) ;
  C4 flatten dédupliqué (`unwind_coverage`/`uncovered_job` dans exec.rs),
  fills orphelins débouclés, repompage filet de sécurité ;
  M5 baseline reconcile exigée + divergence→halt ; M6 retry débouclage ×5 ;
  Mo1 events 2147/2164→Dead, 2172→halt, watchdog spots 30 s ;
  Mo2 RPC vérifie payloadType ; Mo3 warm-up 100 ticks/symbole ;
  Mo4 breaker sur échecs consécutifs ; Mo5 partiels OrderError comptés ;
  Mo8 `deal.commission` journalisée ; M7 IOC_TOL=2,08 bp (3×τ) ;
  m3 `q_min` câblé ; `symbolName` tolère « EUR/USD ».
- Mo7 (choix de conception, non corrigé) : le POC n'évalue que des instants
  échantillonnés (move_gate 0,25 / eval_every 500 ms) ; le live évalue à
  chaque tick — l'ensemble des trades diffèrera, la fréquence detect est
  indicative seulement.
- Nouveau holdout propre = 27/10 (le 26/10 est « brûlé » — M2 : réutilisé
  plusieurs fois pendant les campagnes). Recalibrage sous Convention A
  stricte requis avant tout mode trade.
- SSH serveur durci : `PasswordAuthentication no`, `PermitRootLogin
  prohibit-password`, `KbdInteractiveAuthentication no`
  (`/etc/ssh/sshd_config.d/00-arb-hardening.conf`).

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

## Nouveau broker ECN négocié (utilisateur, 02/11/2026) — chemin prioritaire
- **Commission 2 $ A/R/100 K** (1 $/côté) — vs 3 $/côté IC : économie ~×2
  et épisodes détectés ×3,3 sur le même corpus (A/B dans
  `MC/POC_IC_REEL_2026-11-02.md` — comptabilité cohérente via
  `Engine::comm_per_side`, défaut 3,00).
- **API : FIX 4.4** — adaptateur `fix_client/` en cours : codec + session
  + market data V/W/X top-of-book faits (18 tests) ; reste transport TLS,
  ordres IOC + ExecutionReport (attente spec venue + creds UAT).
- **Couverture : les 28 FX + XAUUSD/XAGUSD** (métaux = upside POC plus tard,
  production reste FX-only).
- **NETTING configurable** — exigé, à verrouiller côté compte.
- **UAT demo disponible.**
- **Colocation Linux** : ~1 µs serveur, ~1 ms distance annoncée, 10 Gbps —
  chiffres à mesurer en réel, pas à calibrer dessus.
- Dépendances encore externes : doc FIX venue (dictionnaire tags, types
  d'ordres supportés, modèle MD), credentials UAT (jamais dans le chat/git).

## Vérification
- `python test_pipeline.py <dossier_src>` sur le serveur (13 vérifications attendues).
- `python test_r7.py` sur le serveur : T1-T6 — quote future, rembobinage pos,
  rejouabilité inter-trials (post-R7).
