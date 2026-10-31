# ctrader_client — adaptateur de production

Adaptateur cTrader Open API pour le moteur `opengraph` — chemins ouverts FX,
Convention A. **Aucune stratégie triangulaire** (les exemples du guide PDF sont
informatifs uniquement).

## Architecture

```
io.rs       FrameIo : trame 4B-BE + TLS rustls + mock MemIo (tests)
codec.rs    envelope ProtoMessage <-> octets (quacky)
net.rs      spawn() -> NetHandle { outbound, inbound } + recv task
pending.rs  corrélation requêtes par clientMsgId (oneshot)
auth.rs     app_auth (2100) -> account_auth (2102) -> Trader (2121, garde NETTED)
            + account list (2149) + refresh token (2173)
heartbeat.rs émetteur 10 s + watchdog 35 s (sur LastRx partagé)
symbols.rs  28 paires FX -> symbolId venue (SymbolsList 2114 + SymbolById 2116)
spots.rs    SpotEvent (2131) -> Quote (digits, ts venue sinon local)
orders.rs   LegIntent LIMIT IOC (touche +-IOC_TOL) + tracker fills
exec.rs     machine séquentielle : jambe N+1 après résolution terminale de N,
            abort -> positions résiduelles -> unwind IOC, parity exec_settle
reconcile.rs ReconcileRes (2124) venue vs pos_deltas internes -> divergences
journal.rs  JSONL par jour UTC — source de vérité post-mortem
session.rs  fenêtre 07:00-21:00 Europe/Zurich + flatten 15 min avant close
config.rs   TOML — secrets hors dépôt
main.rs     bootstrap + boucle stratégie + watchdog + reconnect
```

## Paramètres figés (holdout-validés)

`ddd_min = 2.5 USD`, `cooldown_s = 0`, `notional = 100_000 USD`,
`comm = 3 $/côté/100K`, fenêtre 07:00-21:00 Zurich, pas d'exposition overnight.
T̃/régime = télémétrie seulement (pas de gate).

## Séquence de mise en production

1. **detect_only** : pas d'envoi d'ordre — le journal enregistre chaque
   opportunité (t0, chemin, net, quotes) + ticks. Vérifier que le flux
   correspond à la microstructure réelle IC Markets pendant quelques jours.
2. **demo** : compte NETTED démo. `spawn` réel sur `demo.ctraderapi.com:5035`.
   Vérifier : fills, partiels, rejets, résiduels, unwinds, reconcile 30 s,
   flatten 21:00. Objectif : une semaine profitable.
3. **live** : migration vers le serveur Linux du datacenter ECN +
   `live.ctraderapi.com:5035` + mêmes paramètres.

## Déploiement serveur (/opt/arb)

```bash
# build (depuis le repo)
cd ctrader_client && cargo build --release
# binaire : target/release/ctrader_client[.exe]
# config réelle : /opt/arb/ctrader/config.toml (chmod 600, hors git)
# journals : /opt/arb/ctrader/journal/
```

Le binaire est auto-suffisant : pas de base, pas de service externe —
reconnect/backoff/circuit intégrés.

## Invariants audités (tests intégrés, 49 verts)

- corrélation ordre = `order.clientOrderId` (pas `clientMsgId`) ;
- jambe N+1 envoyée seulement après résolution terminale de N ;
- `frac_eff` sur quantité envoyée (miroir `oq` du POC) ;
- abort ne crédite pas la jambe échouée ;
- résiduel = positions nettes par symbole -> unwind IOC opposé ;
- compte non-NETTED -> refus au démarrage (`ClientError::NotNetted`) ;
- volume venue = centièmes d'unité (1 lot = 10_000_000).
