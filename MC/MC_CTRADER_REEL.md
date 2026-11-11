# Monte Carlo sur ticks réels cTrader — 11/11/2026

## Source de données (pas de générateur)

- Journal du bot en production : `C:\arb_og_trade\*.jsonl` (10-11/11)
- **451 705 ticks réels**, 28 paires, span **14,25 h** (session overnight)
- Replay via `opengraph.Engine.live()` + `on_tick` — même arithmétique
  que le moteur Rust de production (panier complet corrigé)
- Script : `audit/mc_ctrader_reel.py` · résultats : `MC/mc_ctrader_reel_results.json`

## Résultat brut

| Scénario | Évals | Candidats | net>0 | Envoyés | Execs | PnL |
|---|---|---|---|---|---|---|
| ctrader_lat280 (comm 0.40, lat ~280ms) | 292 703 | 259 114 | **0** | 0 | 0 | 0 $ |
| fixlike_lat3 (comm 0.10, lat ~3ms) | 292 703 | 259 114 | **0** | 0 | 0 | 0 $ |

Distribution des nets sur le feed réel : 393 286 évals < −2 $, 11 évals
dans [−2, 0). **Meilleur net observé sur 14,25 h : −0,94 $.**

## Falsification — le zéro est réel, pas un bug

| Contrôle | Résultat |
|---|---|
| Injection dislocation CADJPY ×0.995 / 60 s (même code replay) | **500 détections ≥ 0,4 $, max +47,24 $** → le détecteur tire quand une incohérence existe |
| Livre cohérent synthétique (mc_realiste) | 0 trade → pas de faux positif générateur |
| Live post-patch (depuis restart 08:11) | **0 opportunity** journalisée |
| Live pré-patch (même nuit, binaire fantôme) | 29 opportunities `legs=1`, net ≤ 0,17 $, toutes `traded:false` → artefacts du modèle 1-jambe |

## Interprétation

Le feed retail cTrader overnight est **cohérent à ~1 $ près** à l'échelle
10 K : les désynchronisations observées (≤ 0,9 $ brut) restent sous le
hurdle du panier complet (~2,4 $ commissions + ~4–6 $ spread/débouclage
IOC à 10 K). La baisse de latence (280→3 ms) et de commission (0,40→0,10)
ne change rien : **il n'y a pas d'edge à capturer sur ce feed**, même
gratuit et instantané.

Conséquences :
- Les MC synthétiques positifs (mc_fix44 +41 $/sess) décrivent un feed
  AVEC désynchronisations — pas ce feed retail. L'écart entre les deux
  = exactement la question que l'UAT FIX devra trancher.
- Le bot déployé est sain : il détecte correctement zéro — conforme au
  replay. Aucun trade réel ne devrait partir tant que le feed reste
  aussi cohérent.
- Le recalibrage sous modèle panier reste ouvert, mais sur ce feed la
  variable décisive n'est pas le seuil : c'est la **richesse en
  désynchronisations**, qui dépend du venue — argument supplémentaire
  pour le FIX/ECN.
