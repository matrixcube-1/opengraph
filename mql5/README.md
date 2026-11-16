# OgArb — EA MetaTrader 5 (open-path FX)

Port MQL5 du détecteur open-graph (Rust = autorité sémantique, Java/JForex =
port JCloud). Même sémantique **Convention A** : le net d'un panier complet
`[USD→s0] + jambes + [end→USD]` aux prix **exécutables** (bid/ask), jamais de mid.

## Fichiers

| Fichier | Rôle |
|---|---|
| `OgCore.mqh` | Moteur pur : graphe, chemins (DFS ≤3 jambes), `evalAll`. Aucun appel venue → testable. |
| `OgArb.mq5` | EA : polling `SymbolInfoTick`, médianes de spread, gates, journal JSONL, exécuteur panier IOC. |
| `OgSelfTest.mq5` | EA de test (Strategy Tester, mode *Math calculations*) : 8 tests dont injection +0.75 ¥. |

## Installation

Copier les 3 fichiers dans `<Terminal>/MQL5/Experts/OgArb/`, compiler `OgArb.mq5`
(MetaEditor F7), attacher à n'importe quel graphique. L'EA évalue **toutes** les
paires configurées par timer (`InpEvalMs`), quel que soit le symbole du chart.

## Paramètres (`input`)

### Univers
- `InpSymbols` — vide = les 28 paires Annexe J.2 ; sinon liste `EURUSD,GBPJPY,...`
- `InpSymPrefix` / `InpSymSuffix` — nommage broker (`"pro."`, `".r"`, `"m"`…)
- `InpMaxLegs` — jambes FX max (1..3). `2` ⇒ 392 chemins, `3` ⇒ 2 072.

### Économie (Convention A)
- `InpNotionalUSD` — notionnel par panier (déf. 10 000)
- `InpCommPer100K` — commission $/côté/100K USD (déf. 4.00 — max mesuré)
- `InpCommScale` — multiplicateur (0 ⇒ test sans frais)
- `InpDddMin` — seuil de détection net $ (déf. 0.27)

### Gates
- `InpFreshMs` — fraîcheur max d'une quote (déf. 8 000 ms ; gap-tolérant, p99 réel 6.1 s)
- `InpSpreadCap` — cap spread × médiane roulante (déf. 1.76)
- `InpMedWindow` — fenêtre médiane en ticks (16..256)
- `InpWarmupTicks` — ticks requis/symbole avant évaluation (déf. 100)

### Cadence
- `InpEvalMs` — période du timer d'évaluation (déf. 500 ms, comme le replay)
- `InpHbSec` — heartbeat journal (déf. 60 s)
- `InpSessFromUTC` / `InpSessToUTC` — filtre session (−1 = 24h ; gère le minuit)

### Exécution — **désactivée par défaut**
- `InpAllowLive` — ⚠ `true` envoie de vrais ordres séquentiels IOC
- `InpAllowHedgedAcct` — tolère un compte **HEDGING** en live (déf. `false` → fail-closed)

> **Mode de marge** : l'EA lit `ACCOUNT_MARGIN_MODE` au démarrage et le journalise.
> En live, un compte **NETTING** est requis (même convention que le client cTrader :
> la jambe de sortie compense la position d'entrée). Un compte HEDGING fait échouer
> `OnInit` sauf `InpAllowHedgedAcct=true`. La détection fonctionne sur les deux modes.
- `InpTauSlipBp`, `InpQMin`, `InpMinSeqGapMs`, `InpBreakerFails`, `InpMagic`

### Journal
- `InpLogFile` — JSONL dans `MQL5/Files` commun : `EVT/HB/EVAL/OPP/EXEC/ABORT`
- `InpLogTicks` — log chaque éval (très verbeux)

## Journal

```
{"type":"HB",...,"ticks":N,"warmed":"28/28","evals":N,"det":N,"maxNet":X,"path":i}
{"type":"OPP",...,"net":X,"pi":i,"ddd":Y,"p":"s0=USD ->EURUSD- ->EURJPY+ end=JPY"}
```

## Tests (headless)

```bat
MetaEditor64.exe /compile:"...\MQL5\Experts\OgArb\OgSelfTest.mq5" /log
terminal64.exe /config:ogtest.ini
```
`ogtest.ini` : `[Tester] Expert=OgArb\OgSelfTest.ex5, Model=3 (math), ShutdownTerminal=1`.
Logs : `MetaQuotes\Tester\<id>\Agent-127.0.0.1-3000\logs\`.

**Résultat : 8 PASS / 0 FAIL** — 2 072 chemins (392 à L≤2), livre cohérent −4.76 $,
injection +0.75 ¥ → +38.31 $ via `USD→EURUSD→EURJPY→JPY`, gates stale/spread OK,
fees=0 remonte le plancher à −3.00 $.

## Limites connues

- Devise compte supposée USD pour le notionnel net (comme le modèle scellé).
- Exécuteur : jambes envoyées séquentiellement au marché ; un abort laisse les
  positions remplies ouvertes (unwind manuel pour l'instant — même surface que
  `OgStrategy` détect-only).
- Métaux/bouts non inclus — `addSym` accepte toute paire XXXYYY à 6 lettres dont
  les deux devises ∈ univers ; extension métal = travail ultérieur.
