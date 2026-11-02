# PoC IC Markets — première mesure réelle (2026-11-02)

**Instrument** : cBot `OgDetect` (port fidèle `eval_at`, détection seule,
zéro ordre possible) sur cTrader Desktop, compte démo IC Markets NETTED,
paramètres trial #87 (fresh 909 ms, cap 1.76, comm ×1.10, ddd_min 2.66$,
notionnel 100 kUSD). Self-test de parité au boot : livre cohérent → net<0,
distorsion CADJPY → chemin gagnant correct (validé contre le moteur Rust).
**Fenêtre mesurée** : 18:01→18:51 UTC (soirée calme, post-Londres).

## Résultats — détection live (dernier run, 49,5 min d'éval)

| Métrique | Valeur |
|---|---|
| Ticks capturés (28 symboles) | 43 704 |
| Détections net ≥ 2.66$ | 1 076 |
| **Épisodes** (même chemin, ≤909 ms entre détections) | **699 en 49,5 min ≈ 847/h** |
| Durée de vie épisode | médiane 0 ms (flash) · p90 747 ms · max 3,2 s |
| Épisodes > 1 s | 55 |
| Net détecté | mean **+4.12$** · max +8.91$ · min +2.69$ |
| Jambes | 1 : 867 (81%) · 2 : 194 (18%) · 3 : 15 (1%) |
| Chemins | **100% famille CHFJPY** (1 jambe directe dominante, + variantes USDJPY/GBPCHF/CADCHF/EURCHF/AUDJPY) |

Fenêtre du cluster : ~18:01→18:51 UTC — **~50 min de désynchronisation
continue**, puis fermeture nette (nets en décroissance vers le seuil dans
la queue, signature d'un repricing LP). L'éval devient silencieuse quand
l'edge meurt — pas d'hallucination.

## Replay exécution (moteur Rust réel, exec_seq)

354 épisodes rejoués sur les ticks capturés (lat 2,5–10 ms, τ 0,69 bp) :

| Métrique | Valeur |
|---|---|
| Résolutions | 100% `done`, 0 abort |
| PnL réalisé | mean **+4.27$** · p50 +4.45 · min −4.04 · max +9.94 |
| Survie détecté→réalisé | **94%** (+4.52 → +4.27) |

## Interprétation

1. **Le phénomène existe réellement chez IC** — quotes recalculées à
   cadence propre (synthèse LP) → désynchronisations vs valeur
   triangulaire, exactement le mécanisme du MC. Fréquence ~50×
   l'hypothèse prudente (~10-15/j).
2. **Mono-cluster** : une seule quote (CHFJPY) alimente tout ce soir.
   L'argmax capture préférentiellement le chemin le plus court —
   comportement sain (coût minimal).
3. **Dominance de flashs <1 s** à la granularité du poll (~1-2/s) :
   indiscernable entre flash tradable et auto-correction venue — la
   borne sur l'exécutabilité vient de là.

## Limites déclarées

- **Granularité** : poll OnTick ~1-2/s → mouvement intra-seconde
  invisible ; fills 2,5-10 ms paraissent parfaits (borne optimiste PnL,
  borne pessimiste fréquence/durée de vie).
- **Pas de profondeur L1** dans le flux cBot (comme le flux Open API).
- **Pas de compétition course** modélisée — victoire sur la quote
  supposée (borne optimiste connue du MC).
- Compte démo — le flux live peut différer (cohérence des désynchros
  à re-vérifier sur prod plus tard).
- **Session gate** : les évals s'arrêtent à 20:45 Zurich par design —
  données nocturnes = ticks seuls.

## Prochaine étape

Session de Londres demain (07:00 Zurich reprise auto des évals) :
distribution en régime actif, diversité des clusters, taille des edges.
La mesure définitive de l'exécutabilité reste le client Rust au débit
natif des ticks (10-50/s/symbole) — en attente de l'approbation Spotware.

## Addendum — scénario broker ECN $2 A/R + colocation ~1 ms (A/B propre)

A/B re-simule sur le meme corpus (journal du 02/11, 71 210 ticks/1,67 h)
avec `Engine::comm_per_side` — le moteur applique desormais **le meme
taux** a l'eval, aux fills exec_seq ET a la commission de liquidation
(comptabilite coherente par construction, plus de correction post-hoc) :

| Metrique | $3/cote (IC) | **$1/cote (ECN negocie, 2$ A/R)** |
|---|---|---|
| Episodes detectes | 1 794 (1 074/h) | **5 862 (3 505/h) — x3,3** |
| Net detecte moyen | +4.53 $ | **+7.64 $** |
| PnL exec realise (n=400) | +4.33 $ | **+7.30 $ — +69%** |
| p50 / min / max | +4.47 / -4.04 / +9.94 | +7.90 / -4.25 / +12.58 |
| Survie detecte->realise | 95 % | **96 %** |
| Resolutions | 100 % done | 100 % done |

Chemins 3 jambes presents a $1/cote (ex. EURCAD->EURCHF->CHFJPY +9.52 $
realise) — le seuil plus bas les rend rentables apres 3 commissions.
A ~1 ms de distance colocalisee, les fills precedant le repricing
(episodes p90 747 ms, max 3,2 s) deviennent quasi-certains : la borne
optimiste MC devient realiste. Caveat inchange : granularite du poll
cBot ~1-2 Hz — la mesure definitive reste le feed natif.

**Venue confirmee** (02/11 soir) : FIX 4.4, les 28 paires Annexe J +
XAUUSD/XAGUSD, compte NETTING configurable, UAT demo disponible.
Adaptateur `fix_client/` demarre : codec tag=value/CheckSum + session
logon/seqnum/heartbeat + market data V/W/X top-of-book (18/18 tests).
Reste : transport TLS, ordres IOC + ExecutionReport sur spec venue.
