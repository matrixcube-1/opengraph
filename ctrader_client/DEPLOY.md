# ctrader_client — guide de mise en production (FX-only)

Adaptateur cTrader Open API du moteur `opengraph` : chemins ouverts FX ≤ 3 jambes,
Convention A, compte **NETTED**. Aucune stratégie triangulaire / cycle fermé.
Métaux : implémentés dans le POC uniquement, **en stand-by** (non portés, non activés).

## 1. Ce qu'il reste à fournir : uniquement les credentials

Tout le reste est dans le dépôt. Les secrets passent **exclusivement** par
variables d'environnement (jamais dans un fichier versionné, jamais loggés) :

| Variable | Source |
|---|---|
| `CTRADER_CLIENT_ID` | app créée sur https://openapi.ctrader.com |
| `CTRADER_CLIENT_SECRET` | idem |
| `CTRADER_ACCESS_TOKEN` | OAuth de l'app sur le compte démo IC Markets (scope trading) |
| `CTRADER_REFRESH_TOKEN` | optionnel — renouvellement du token (2173) |

Et dans le TOML : `ctid_trader_account_id` du compte démo NETTED
(`0` accepté en `mode = "detect"` : résolu via la liste du token ; **obligatoire** en `trade`).

Fichier d'environnement serveur recommandé : `/opt/arb/ctrader/secrets.env`
(`chmod 600`, propriétaire du service, hors git).

## 2. Paramètres calibrés (figés — ne pas modifier sans recalibrage)

Source : Optuna `poc_exec_fxcur`, trial #87 (28 FX, 22+23/10 in-sample, holdout 26/10).

| Paramètre | Valeur | Rôle |
|---|---|---|
| `fresh_ms` | 909 | âge max d'une quote utilisable |
| `spread_cap` | 1.76 | spread max = 1.76 × spread médian du symbole |
| `comm_scale` | 1.10 | marge de sécurité de +10 % sur la commission |
| `ddd_min` | 2.66 USD | net minimal (après commission) pour déclencher |
| `q_min` | 0.5 | fraction de fill minimale, sinon abort + unwind (câblée — audit m3) |
| `leg_timeout_ms` | 2000 | résolution max d'une jambe IOC |
| notionnel | 100 000 USD | ≈ 1 lot par jambe (0,75–1,7 lot selon la base) |
| commission modèle | 3 $ / côté / 100 k unités de base | à confirmer sur les premiers fills démo (`commission` journalisée, Mo8) |
| tolérance IOC | ±2,08 bp (`IOC_TOL` = 3×τ calibré 0,69 bp — audit M7) | limite = touche ± tol, arrondie aux digits |
| fenêtre | 07:00–21:00 Zurich, lun–ven | flatten à partir de 20:45, rien overnight |

`cooldown` calibré ≈ 0,05 s : équivalent au comportement live (une seule séquence
à la fois, nouvelle séquence dès la précédente résolue). Indicateurs de régime :
télémétrie seulement, aucun gate.

**Attente réaliste (Convention A exécutable — chiffres corrigés post-audit)** :
holdout 26/10 = **+61,58 $ / 12 cycles** sur 24 h (vs +955 $ sous l'ancien marquage
au mid — bug C1), dont ≈ **+54,92 $ / 11 cycles dans la fenêtre Zurich**. Le résiduel
est désormais **liquidé immédiatement** en fin de séquence (file d'unwind — C2),
comme le marque la simulation ; rien n'est porté jusqu'au flatten. La capture réelle
sera inférieure (rejets, slippage, flux IC ≠ Dukascopy, coût des débouclages réels
≈ 1 ordre par jambe remplie).

**Correctifs de l'audit externe (tous implémentés)** :
- C1 : valorisation exécutable partout (POC, eval.rs, exec.rs, pybind).
- C2 : résiduel liquidé immédiatement après `Done` — plus d'accumulation directionnelle.
- C3 : plus de `sync(t0)` en live (les prix ne reculent jamais après un fill).
- C4 : flatten dédupliqué contre la file d'unwind (`unwind_coverage`/`uncovered_job`) ;
  fills orphelins (seq avortée en vol, fill tardif « L ») débouclés immédiatement ;
  repompage filet de sécurité à chaque itération.
- M5 : baseline reconcile exigée avant tout trade ; divergence à l'arrêt → halt + flatten.
- M6 : débouclage retenté jusqu'à 5 fois (`UNWIND_MAX_ATTEMPTS`), reliquat IOC repompé.
- Mo1 : events 2147/2164 → reconnexion ; 2172 → halt + flatten ; watchdog spots 30 s.
- Mo2 : RPC vérifie le payloadType de la réponse (ErrorRes → `Venue`, jamais un faux succès).
- Mo3 : warm-up 100 ticks/symbole avant tout trade (médiane de spread fiable).
- Mo4 : breaker remisé après une session > 60 s (échecs consécutifs seulement).
- Mo5 : fills partiels comptabilisés sur OrderError comme sur timeout.
- Mo8 : `deal.commission` cumulée et journalisée sur LegFill/UnwindFill.
- m3 : `q_min` du TOML câblé dans `PathExec` ; `symbolName` tolère « EUR/USD ».

## 3. Build et installation (serveur Linux, /opt/arb)

```bash
# Rust si absent : curl https://sh.rustup.rs -sSf | sh
cd /opt/arb/opengraph/ctrader_client     # clone du dépôt
cargo test                               # attendu : 56 passed, 0 failed
cargo build --release
install -D target/release/ctrader_client /opt/arb/ctrader/ctrader_client
install -D config/ctrader.toml /opt/arb/ctrader/config.toml   # puis éditer
mkdir -p /opt/arb/ctrader/journal
```

Dans `/opt/arb/ctrader/config.toml` : `env = "demo"`, `mode = "detect"`,
`journal_dir = "/opt/arb/ctrader/journal"`, `require_netted = true`.

Lancement :

```bash
set -a; . /opt/arb/ctrader/secrets.env; set +a
RUST_LOG=info /opt/arb/ctrader/ctrader_client /opt/arb/ctrader/config.toml
```

En service : lancer via systemd (`EnvironmentFile=/opt/arb/ctrader/secrets.env`,
`Restart=on-failure`) et **redémarrer une fois par jour hors session (ex. 21:30
Zurich)** : les séries de ticks et la médiane des spreads s'accumulent en mémoire
depuis le démarrage. Le reconcile de démarrage réadopte toute position résiduelle.

## 4. Séquence de mise en production

### Étape A — `detect_only` (2–3 jours) : zéro ordre
Contrôles au démarrage (journal `System`) : auth app + compte OK, compte **NETTED**
(sinon arrêt `NotNetted`), **28/28 symboles mappés** avec digits/minVolume/stepVolume,
abonnement spots, baseline reconcile.

Contrôles en continu :
- prix ingérés cohérents avec la plateforme cTrader (EURUSD ≈ 1.1x, **USDJPY ≈ 150**, pas 1.5 ni 15 000) ;
- fréquence des `Opportunity` (net ≥ 2,66 $) dans la fenêtre. En `detect`, une même
  opportunité persistante est journalisée à chaque tick : compter les **épisodes**
  (ticks consécutifs sur le même chemin), à comparer aux ~77 cycles/jour de la sim.
  Beaucoup moins ⇒ l'edge venait du flux Dukascopy : on s'arrête là ;
- distribution du `net_usd` et répartition horaire.

**Capture du flux (calibration venue)** : `record_ticks = true` (défaut)
écrit chaque quote reconstruite dans `journal_dir/ticks/YYYY-MM-DD.jsonl`
(`{"ev":"tick","e":i,"bid":..,"ask":..,"ts":..}`, ~50–150 lignes/s). Pipeline
live : `audit/ticks_to_poc.py convert TICKS_DIR OUT_DIR` produit les CSV
horaires au format `load_sym`, puis `scan OUT_DIR DAY...` → `cand_*.npz` →
même chaîne Optuna que la calibration Dukascopy. Les paramètres recalibrés
sur flux IC remplacent alors les #87 (calibrés Dukascopy) avant `trade`.

### Étape B — démo `trade` (1 semaine)
`mode = "trade"`, `ctid_trader_account_id` renseigné. À vérifier dans le journal :
- jambes envoyées / remplies / partielles / rejetées, `lat_ms` tick→envoi ;
- **aucun rejet `INVALID_REQUEST` (digits du prix)** ou volume (min/step) ;
- unwinds exécutés après abort ET après chaque séquence terminée (liquidation C2),
  `Reconcile.divergent = false`, aucun `unwind_exhausted` ni `manual_check` ;
- flatten 20:45–21:00 effectif, **aucune position overnight** ;
- commission réellement débitée par fill vs modèle (3 $ / 100 k base) — champ
  `commission` des événements `leg_fill`/`unwind_fill`.

### Étape C — live
Uniquement si la semaine démo est validée (critères §5). `env = "live"`
(`live.ctraderapi.com:5035`), mêmes paramètres, même binaire.

## 5. Critères go / no-go et arrêt

| Critère | Go | Arrêt immédiat |
|---|---|---|
| Opportunités sur flux IC | fréquence comparable à la sim | quasi nulles |
| Net réalisé moyen / cycle | > 0 après commission réelle | ≤ 0 sur 3 jours |
| Rejets / kills IOC | faibles | majorité des jambes |
| Reconcile | sans divergence persistante | divergence non expliquée |
| Overnight | 0 position | toute position après 21:00 |

Arrêt : stopper le service, vérifier les positions sur la plateforme, fermer
manuellement tout résiduel.

## 6. Corrections de conformité venue (audit pré-démo, doc officielle cTrader)

1. **Prix SpotEvent** : toujours en 1/100000 d'unité (pas 10^digits) — les 7 paires
   JPY auraient été fausses d'un facteur 100.
2. **SpotEvent à un seul côté** : cTrader n'envoie que le côté qui change ;
   forward-fill par symbole (`State.last_px`) au lieu de jeter l'event.
3. **`limitPrice` arrondi aux digits** du symbole (SELL vers le bas, BUY vers le haut) :
   sinon rejet `has more digits than symbol allows` sur chaque ordre.

## Architecture

```
io.rs        FrameRd/FrameWr : trame 4B-BE + TLS rustls + mock MemIo
codec.rs     envelope ProtoMessage <-> octets
net.rs       tâches lecteur/écrivain indépendantes (cancel-safe) -> NetHandle
pending.rs   corrélation requêtes par clientMsgId
auth.rs      app_auth 2100 -> account_auth 2102 -> Trader 2121 (garde NETTED)
heartbeat.rs émission 10 s + watchdog 35 s
symbols.rs   28 FX -> symbolId (2114 + 2116), prix 1e5, round_px, volumes
spots.rs     SpotEvent 2131 -> Quote (forward-fill demi-côté)
orders.rs    LIMIT IOC (touche ± IOC_TOL, arrondi digits) + fills (filledVolume)
exec.rs      séquence : jambe N+1 après résolution de N ; abort -> unwind IOC
reconcile.rs 2124/2125 venue vs interne ; baseline adoptée au démarrage
journal.rs   JSONL par jour UTC — source de vérité post-mortem
session.rs   07:00–21:00 Europe/Zurich (DST), flatten 15 min avant close
config.rs    TOML + secrets par env
main.rs      bootstrap + boucle + housekeeping + reconnect/backoff/circuit breaker
```

## Invariants testés (60 tests)

corrélation par `order.clientOrderId` ; jambe N+1 seulement après résolution de N ;
fills crédités sur `deal.filledVolume` ; `frac_eff` sur la quantité envoyée ;
abort ne crédite pas la jambe échouée ; résiduel = position nette par symbole -> unwind ;
compte non-NETTED refusé ; volume = centièmes d'unité (1 lot = 10 000 000) ;
prix ×1e5 ; limit sur la grille digits ; pas de nouvelle séquence en fenêtre de flatten.

## Annexe — cBot `cs/OgDetect.cs` (pont optionnel, détection seule)

cBot cTrader Automate qui porte `eval_at` (chemins ouverts, Convention A) —
**aucun appel d'ordre, structurellement incapable de trader**. Rôle : mesurer
la fréquence et la durée de vie des opportunités réelles chez IC Markets
**avant** l'approbation Open API Spotware (les cBots n'en ont pas besoin —
ils tournent sur le compte déjà connecté dans cTrader Desktop).

Installation : cTrader Desktop -> Automate -> New cBot -> coller le fichier
-> Build -> lancer sur un graphique EURUSD du compte DÉMO (l'accès disque
demande AccessRights.FullAccess, déjà déclaré).

Journal : `<JournalDir>/<YYYY-MM-DD>.jsonl` — même schéma que `journal.rs`
(`{"ev":"tick","e":0..27,"bid":..,"ask":..,"ts":..}`) -> lu tel quel par
`audit/ticks_to_poc.py` ; opportunités : `{"ev":"opportunity",...,"traded":false}`.

Garde-fou de parité : au boot le bot évalue un livre synthétique à distorsion
connue (même fixture que `engine/src/eval.rs` tests) et **refuse de démarrer**
si l'argmax ou le signe du net diverge — vérifie le port sans compilateur externe.

Divergences déclarées vs le moteur Rust (télémétrie, pas trading) :
fraîcheur par détection de changement bid/ask (pas de ts venue par symbole en
Automate) ; médiane de spread fenêtrée 512 ticks vs historique complet ;
évaluation à chaque tick du symbole hôte (poll des 28) ; aucune exécution.
