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
- Serveur Vultr `104.238.181.77` : **appartient à un AUTRE projet
  (clarification utilisateur 10/11/2026) — ne pas utiliser pour
  Arbitrage_FX sans autorisation explicite.** Le corpus Dukascopy qui
  y réside (`/opt/arb/data`) est à considérer indisponible ; données de
  travail locales = `C:\arb_cs_journal\` (cBot 2-4 nov.) +
  `journal/_archive\` (sessions Rust).
  (Historique 06/11 : durcissement non appliqué —
  `PasswordAuthentication yes` + `PermitRootLogin yes`, clé
  `~/.ssh/id_ed25519` installée, `arb_ed25519` refusée. Reste la
  responsabilité de l'autre projet, pas la nôtre.)

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
- `ssh_run.py` : `ARB_HOST` + clé SSH (pas de mot de passe dans le code).
  Clé effectivement installée sur la box : `~/.ssh/id_ed25519`
  (`arb_ed25519` est refusée — Permission denied, 06/11).
  ⚠ 10/11 : la box appartient à un autre projet — scripts SSH
  inutilisables pour Arbitrage_FX sans autorisation explicite.

## POC 3 jours (serveur $ARB_HOST, /opt/arb/poc)
- ⚠ 10/11 : $ARB_HOST = box d'un autre projet — voir note ci-dessus.
  Procédures ci-dessous conservées comme historique documentaire.
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
- **Commission** : nominale 6 $ AR/100 K (3 $/côté — `COMM=3.00` dans
  poc_engine). **Mesurée sur 37 fills réels cTrader demo (10/11) :
  3,63–4,00 $/côté → le client live utilise `COMM_PER_100K_USD = 4,00`
  (max observé, conservateur).** Toutes les calibrations existantes
  (#87, corpus cBot) sous-évaluent les frais — recalibrage requis.
  Paliers de volume : peut descendre jusqu'à 3 $ AR/100 K (1,50 $/côté) — upside
  futur, `comm_scale` planché à 1,0 pour la campagne PoC (ne pas calibrer sur
  un taux non confirmé).
- **Langage cible production : Rust** — le POC Python sert à valider la
  méthodologie, les données et l'économie ; l'engine de production sera
  réimplémenté en Rust (décision post-PoC).
- Cadrage : uniquement ce qu'IC Markets considère acceptable (univers Annexe J,
  PDS S1-S4, pas de CME dans l'implémentation IC).

## Client cTrader Open API (`ctrader_client/`) — état 10/11/2026
- Implémenté + testé : 60/60 tests, auth réelle validée sur
  `demo.ctraderapi.com:5035` (app_auth → ctid → account_auth), pipeline
  complet prouvé sur `mock_spotware` (kill/restart venue : re-bootstrap +
  re-warm-up fail-closed + détection reprise — voir DEPLOY.md annexe B).
- Fix rustls `CryptoProvider::ring` installé au boot (commit 9202b4c).
- Comptes : demo NETTED de travail = ctid `49449511` (compte `10133694`,
  ~100 k$) ; live identifié = ctid `49449505` (compte `6192336`, solde 0).
  L'ancien compte `49404217` (HEDGED) n'est plus le compte de travail.
  Mode Trade exige `ctid_trader_account_id` explicite + NETTED.
- Secrets dans `C:\arb_secrets\ctrader_ultragraph.env` — JAMAIS dans git.
- systemd prod : `Restart=always` OBLIGATOIRE (breaker sort en exit 0 —
  `on-failure` ne relancerait pas après un blip venue, voir DEPLOY.md §3).
- cBot OgDetect (`ctrader_client/cs/`) — détect-only par construction
  (aucun ordre). Corpus 2-4 nov. ~180 Mo dans `C:\arb_cs_journal\`.
  ⚠ 10/11 : resynchronisé sur l'économie live — NOTIONAL 10K,
  `COMM_100K=4,00` → `COMM=comm_usd()=0,40`/jambe (AVANT : formule
  implicite 100K — à 10K sans ce fix la comm était ×10 trop forte),
  FreshMs 8000, DddMin 0,27 dans `cs/config.json`. Mêmes lignes
  journal que le client Rust (ticks + opportunity + system).

### Paramètres live mesurés sur venue réelle (session 10/11/2026)
- `NOTIONAL_USD = 10 000` — ddd_min scalé 2,66 → 0,27 $ (×10 vs #87@100K).
- `COMM_PER_100K_USD = 4,00` — mesuré 3,63–4,00 $/côté sur 37 fills
  réels ; les calibrations #87/#985 (comm 3,00) sous-évaluent les frais.
- `fresh_ms = 8 000` — flux de ticks normal : pas de heartbeat, la derniere quote recue reste l'etat courant ; aux heures creuses les gaps inter-ticks atteignent des secondes par symbole. Une quote inchangée
  RESTE exécutable (dernière quote = état courant). p99 des gaps réels
  = 6,1 s → 8 s. À 909 ms la règle tuait ~41 % du temps les nœuds
  devises en flux calme (A/B replay : 909 → 0 épisode, ≥2000 →
  détections). Le watchdog 30 s couvre déjà la connexion morte.
- Latence wire mesurée ~250–330 ms (timestamps socket, `q_ms`) — écart
  detect→fill ≈ −0,5 à −1 $/cycle (session Trade du matin : −22,96 $
  réalisé). Le PnL détecté (frais inclus, slippage exclu) est une borne
  supérieure théorique, jamais un réalisé — detect après-midi :
  103 épisodes, +187,70 $ théorique.
- Viewers : `telemetry_view.py`/`telemetry.bat` (événements),
  `ticks_view.py`/`ticks.bat` (flux brut), `live_view.py`/`live.bat`
  (unifié ticks + PnL).
- PIÈGE process : boucles superviseurs orphelines respawnent le client
  sans secrets → crash-loops, verrous exe, doubles boots. Après toute
  relance vérifier l'unicité (1 superviseur + 1 client).
- Le repo vit dans OneDrive — verrous/conflits possibles sur journaux
  et `target/` ; déplacer hors OneDrive recommandé avant prod.
- Audit consolidé externe : `audit/Scan_Bugs_OpenGraph.pdf` (+
  extraction `.txt`) — vérifié contre le code le 10/11 : exact sur
  R6/R7/Conv-A corrigés, 43 tests `regime_indicators`, FIX incomplet.
  Avertissements toujours valides : recalibrage Convention A + hold-out
  jour futur requis avant tout mode Trade.
- `cs/OgTrade.cs` (11/11, PANIER COMPLET — bug legs=1 corrigé) : l'eval
  price désormais ce que l'exécuteur envoie : ordres = [USD→devise de
  départ] + jambes path[] + [devise terminale→USD], chaque ordre =
  position réelle, qty chaînée aux fills (ExecuteMarketOrder), garde
  stale MaxRangeBp=2,08 par ordre, partiel<QMin→abort+unwind,
  hold (TargetUsd/StopLossUsd/HoldMaxMs), unwind de toutes les
  positions créées, réconciliation NETTED via History (PnL réalisé des
  positions absorbées), flatten filet de sécurité, 1 séquence à la
  fois, `MinSeqGapMs=2000`, breaker 5 échecs, `AllowLive=false` →
  refuse tout compte live. Journal `C:\arb_og_trade\` :
  leg_send/fill/rej, unwind_fill/fail, settle (pnl, filled, unwound,
  resid_open). Self-test : argmax attendu `[USDJPY, CADJPY]` s0=USD.
  ⚠ 11/11 nuit : le binaire pré-fix a tiré 7 séquences legs=1 (-13,63 $)
  — pricing fantôme entrée mid + sortie non exécutée ; voir
  `audit/CORRECTIONS_LOG.md` §BUG MARGE-PANIER.

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

## Correction coût aller-retour marge (10/11/2026, mesuré sur OgTrade)
- Smoke test demo (ddd=-1) : fills réels ~350 ms, slippage ~0, mais settle
  réalisé ≈ -1,2 $/cycle vs net détecté -0,37. Cause : chaque position marge
  se REFERME à travers le book — l'eval ne priceait que la conversion +
  1 liquidation. Coût réel = (2 traversées + 2 comms)/jambe.
- OgTrade.cs eval : ajout `unwind` = Σ_jambes spread_frac×NOTIONAL + comm
  de clôture (2×comm), moins la liquidation modélisée (terminal ≠ USD).
  Effet : terminal-USD +L closes, terminal≠USD +(L-1) closes.
- ~~Le même biais existe dans engine/eval.rs~~ — CORRIGÉ 11/11 : le
  bug marge-panier (eval pricait entrée+sortie USD non exécutées) était
  identique en Rust/Python/C# ; corrigé partout (voir §BUG ci-dessus).
- Conséquence seuil : un chemin doit détecter ≥ ~+1,2-1,5 $ de net pour
  couvrir l'aller-retour réel à 10K — ddd=0,27 calibré conversion, pas marge.

## Verdict Monte Carlo + corpus réel — économie marge corrigée (10/11/2026)
- Correction portée PARTOUT : engine eval.rs/exec.rs/exec_settle (35/35
  tests), poc_engine.py (eval_at + exec_seq), OgTrade.cs.
- MC falsifiable (mc_realiste, générateur latent cohérent) :
  sync_control 0 trade ; async 186 tr 100% wr +110$/sess ; lat 0,5→25 ms
  +111→+106 ; spread x0,5→x2,0 +121→+101. Edge robuste à lat 25 ms ECN.
- MC conditions cBot réelles (mc_ctrader, lat 350 ms mesurée) :
  38 tr, 8 aborts (kill IOC protège), 76% wr, +35,6$/sess ; ddd quasi
  insensible ; lat 5 ms réf +53,5 — la distance coûte ~33% du PnL.
- Replay corpus RÉEL 31 min (73 881 ticks, replay_recalib.py) :
  net détecté p99,9 = +0,28 ; 143 evals >0 ; 4 séquences réelles,
  wr 75%, +1,69$ ; détecté +0,78 → réalisé +0,85 — parité modèle/réel
  confirmée (le gap -0,8 pré-correction est fermé).
- Lecture honnête : l'edge existe et le modèle est maintenant juste, mais
  le feed cTrader retail offre ~4 fenêtres/30 min à 10K (~+0,4$/trade,
  ~3$/h). La dislocation réelle est ~10x plus fine que celle du générateur.
- OgTrade.cs : mêmes formules que le Rust (unwind/jambe, comm 2x, liq
  remplacée). Params jour : ddd=0.27, fresh=8000, cap 1.76, scale 1.10,
  range 2.08, q_min 0.5, gap 2000, breaker 5, AllowLive=false.

## ⚠ Validité des chiffres ci-dessus (11/11/2026)
- Tous les résultats MC/corpus/épisodes publiés avant le 11/11 ont été
  calculés sous l'ancien modèle (entrée mid + sortie USD non exécutée)
  → nets gonflés, surtout les chemins legs=1 (fictifs). Recalibrage
  complet sous modèle panier + nouveau holdout requis avant toute
  décision de sizing. Le coût réel par cycle a augmenté (≥3 positions
  + ≥3 clôtures) → DddMin à recalibrer sur corpus frais.

## OgTrade launch-ready (10/11/2026 soir)
- `ctrader-cli.exe build` : la DLL `ctrader-cli.dll` dans
  `AppData/Local/Spotware/cTrader/<hash>/app_*/` s'invoque via un
  `dotnet.exe` hôte — installé user-local dans `C:\Users\EVROSIS\.dotnet\`
  (dotnet-install.ps1). Le build host `algohost.netcore` exige
  **Microsoft.AspNetCore.App 10.0.9** (à installer dans `~/.dotnet` aussi,
  sinon « build host stopped unexpectedly ») + desktop cTrader lancé.
  Commande qui marche :
  `& "$env:USERPROFILE\.dotnet\dotnet.exe" "...\ctrader-cli.dll" build "<projet>.csproj" --algo-access FullAccess`
  `run`/`backtest`/`optimize` existent aussi mais exigent ctid+pwd-file.
- OgTrade.cs final : économie marge corrigée (unwind/jambe, comm 2x, liq
  remplacée — parité Rust) + **tolérance IOC logicielle** : avant chaque
  jambe li>0, compare sym.Bid/Ask LIVE (maj plateforme, indépendant de
  OnTick sérialisé) au quote t0 ; écart adverse > MaxRangeBp => abort
  "stale" — même sémantique que tau_slip du moteur. ExecuteMarketOrder
  synchrone seul dispo dans cette API (MarketRange = async uniquement).
- Seuil calibré corpus réel : ddd=0.4 (3/3 wins +1.90$ vs 0.27 3/4 +1.69$ ;
  le seul perdant -0.14 venait d'un net +0.34 marginal). n<=4 => fragile,
  mais 0.4 = filtre bruit détection ~2 sigma.
- Replay sweep corrigé : t_free/lock étaient PARTAGES entre seuils =>
  les seuils hauts étaient affamés ; désormais indépendants.
- Détecté vs réalisé corrigé : +0.78->+0.85, parité modèle confirmée.
- Monitor : `audit/monitor_ogtrade.py` — résume le journal jour :
  séquences, gap réalisé-détecté (attendu ~0 sous nouveau modèle),
  aborts, pnl total.
- Instance GUI : l'instance existante garde SES params sauvegardés —
  vérifier DddMin=0.4 dans le panneau avant Start (le défaut 0.4 ne
  s'applique qu'aux NOUVELLES instances).

## OgTrade v2 — pilotage evenementiel (10/11/2026 nuit)
- Detection declenchee par `Symbol.Tick` sur les 28 paires (la quote
  bouge -> eval immediate) + OnTick chart + Timer 250 ms fallback.
  Mesure live : symTick ~11 evts/s vs onTick ~0.3/s (canal ~40x).
- `OnTimer` heartbeat : resync depuis proprietes live + flush buffer
  ticks + stats canaux (`evt onTick=.. symTick=.. timer=..` toutes 5 min).
- I/O : journal ticks bufferise (AutoFlush OFF) — la latence disque
  sort du chemin de detection ; audit events flush explicitement.
- Settle : PnL REALISE depuis History(PositionId).NetProfit (commissions
  incluses), fallback NetProfit pre-cloture si pas encore indexe.
- Parite C#<->Rust verifiee aux constantes : COMM=0.40$/jambe (4.00/100K,
  max des 37 fills mesures), NOTIONAL=10K, comm_rt=2x, unwind par jambe,
  traversée liq retranchee terminal!=USD.
- Rebuild cAlgo => auto-restart de l'instance observe (le .algo est
  recharge ~8 s apres build).
- Recalibrage soiree (18h-22h) : med gap 1.77s, p90 7.2s ; nets max
  +0.15$ -> ZERO fenetre tradeable ces heures — l'edge se concentre
  aux heures liquides (attendu Tokyo ~01h, Londres ~08h).
