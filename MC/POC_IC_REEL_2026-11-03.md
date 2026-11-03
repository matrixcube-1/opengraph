# PoC sur données réelles — rapport consolidé 02-03/11/2026

**Corpus** : journal cBot OgDetect (detect-only, zéro ordre) sur compte
démo IC Markets — 706 053 ticks, 2 108 opportunités, ~18 h de flux
continu, 0 ligne malformée, 0 quote croisée, 28/28 symboles couverts.

| | Soirée 02/11 (calme) | Journée 03/11 (Londres) |
|---|---|---|
| Ticks | 188 248 | 517 805 |
| Fenêtre | ~6,4 h | ~11,6 h |
| Opportunités (brut) | 1 918 | 190 |
| Épisodes (dédup FRESH=909ms) | 1 794 (1 074/h) | 976 (83,9/h) |
| Net détecté mean / max | +4.12 / +13.06 $ | +6.16 / **+88.20** $ |
| Jambes 1/2/3 | 1 581 / 311 / 26 | 124 / 55 / 11 |

## 1. Santé de la chaîne — validée

- cBot en continu depuis le 02/11 ~18h : rotation de fichier UTC
  propre, aucun FATAL, aucun crash, un seul gap >1 min (6,5 min
  micro-coupure feed, auto-guérie).
- Format journal inchangé, consommé tel quel par `replay_journal.py`
  (moteur Rust réel : `on_tick` + `exec_seq`).
- Self-test de parité au boot passé à chaque redémarrage.
- La collecte nocturne (ticks 24h/24 hors session, évals 07-21 Zurich)
  fonctionne comme prévu.

## 2. Le phénomène est réel et diversifié

- 02/11 : mono-cluster CHFJPY (~2 300 détections, toutes routes
  menant à CHFJPY) — dislocation continue ~50 min puis fermeture
  nette (repricing LP).
- 03/11 : **aucun mono-cluster** — EURCAD, GBPCAD, CADCHF, GBPCHF,
  AUDJPY, AUDCHF, EURAUD… 20+ chemins distincts. Les dislocations
  migrent avec l'activité de session.
- Injection prouvée : l'argmax balaie les 2 072 chemins sans angle
  mort (NZD/GBP-AUD/EUR-NZD clusters provoqués et trouvés).
- Mécanisme confirmé par les quotes : CHFJPY coté ~2,3 pips riche
  vs synthétique USDJPY/USDCHF — le bot vend la quote riche au BID
  (spread payé en entier, jamais de mid).

## 3. Exécutabilité — le nerf de la guerre, honnêtement

`exec_seq` (latence 2,5 ms, IOC ±3τ, partiels, abort, liquidation
Convention A) sur les épisodes réels :

| | Soirée calme | Session Londres |
|---|---|---|
| Résolutions | 100 % done | 100 % done |
| Net détecté moyen | +4.53 $ | +9.90 $ |
| PnL réalisé moyen | +4.33 $ | **+1.51 $** |
| Survie détecté→réalisé | **95 %** | **15 %** |
| p50 / min / max | +4.47 / −4.04 / +9.94 | +0.83 / **−46.37** / +82.17 |

**Lecture** : en régime calme les edges sont petits mais les fills
quasi-parfaits. En régime volatil les dislocations grossissent (+88 $
max détecté, +82 $ réalisé) mais **la quote bouge dans le délai
envoi→fill** : nombreux `det +15-20$ → pnl −5 à −46$`. La variance
est le vrai adversaire — exactement ce que la latence ~1 ms en
colocation ECN est censée réduire (fill avant le repricing).

Caveats structurels inchangés : granularité du poll cBot ~1-2 Hz
(les mouvements intra-seconde sont invisibles — borne optimiste sur
les fills, pessimiste sur la fréquence) ; les extrêmes (+88 $) peuvent
mêler anomalies de quote venue et vraies dislocations.

## 4. Verdict PoC

- ✅ **Binaire validé** : moteur Rust fidèle (parité C#↔Rust), chaîne
  complète tick→éval→journal→replay exécutable sur données réelles.
- ✅ **Le signal existe** : désynchronisations de quotes réelles,
  diversifiées, récurrentes — la rareté n'est pas le goulot.
- ⚠️ **L'exécutabilité est LE problème restant** : survie 95 % en
  régime calme, 15 % en régime volatil à granularité cBot. La mesure
  décisive exige le feed natif à débit réel (FIX ECN, ~1 ms).
- Économie : à 1 $/côté (ECN 2 $ A/R), A/B propre sur corpus 02/11 :
  épisodes ×3,3, PnL/trade +4,33 → +7,30 $ (voir addendum 02/11).

## 5. Prochaines étapes

1. FIX UAT : transport TLS + ordres IOC + ExecutionReport sur doc
   venue (codec/session/MD déjà livrés, 18 tests).
2. Rejouer le corpus 03/11 avec `--comm 1.0` quand le tarif ECN sera
   documenté.
3. Mesurer latence/fill réels sur le serveur colocalisé — ne jamais
   calibrer sur les chiffres annoncés.
4. Garder le cBot en collecte : chaque jour de données affine la
   distribution des régimes (calme vs transition vs volatil).
