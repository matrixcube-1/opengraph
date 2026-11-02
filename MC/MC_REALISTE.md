# MC réaliste — chemins ouverts Convention A

Remplace `Simulation_MonteCarlo_Chemins_Ouverts.pdf` (oct. 2026), jugé
non falsifiable en revue critique (C5) : bruit indépendant par paire →
arbitrages fantômes, pas de coût de sortie, métriques non définies.

Script : `audit/mc_realiste.py` — résultats : `MC/mc_realiste_results.json`.
Reproduction : `python mc_realiste.py 48 30 all` (graine maître 20261102).

## 1. Principe de falsifiabilité

Le MC ne fabrique **aucune** opportunité par construction. Trois garde-fous :

1. **Latent cohérent** — 8 marches log-normales corrélées `up[c](t)`
   (valeur USD par devise : facteur global + idiosyncrasie + sauts Poisson).
   Chaque mid de paire *dérive* : `mid(a,b) = up[a]/up[b]` — cohérence
   triangulaire exacte. Un générateur à bruit indépendant par paire
   inventerait des edges impossibles ; ici ils sont structurellement absents.
2. **La seule source d'edge est temporelle** — chaque paire cote sur sa
   propre horloge Poisson. Quand le latent bouge (saut), la paire ne reflète
   le nouveau prix qu'à son prochain tick : l'état *publié* est
   transitoirement incohérent alors que le latent reste cohérent. C'est le
   mécanisme réel (repricing asynchrone des LP), pas un artefact de bruit.
3. **Contrôle `sync`** — horloge unique partagée : l'état publié est
   toujours cohérent → le MC exige ~0 trade gagnant. Un edge positif
   persistant en mode sync invaliderait le générateur ou le moteur.

## 2. Moteur exécuté — le vrai, pas une réimplémentation

Chaque session passe par `opengraph` (Rust production, feature python) :

- ingestion `on_tick` (mêmes gardes que le live : ordre ts, quotes
  croisées/NaN rejetées) puis `eval_at(t)` par timestamp — état forward-fill,
  fraîcheur bilatérale, cap spread × médiane incrémentale ;
- `exec_seq` E1-E9 : fills séquentiels au dernier tick ≤ t_fill (vraie
  latence), kill IOC > 3τ, partiel 0.6, résiduel liquidé Convention A
  (exécutable, jamais mid), commissions `COMM/up[base]`, coût d'abort par
  arête (`med_rel × notional + comm`, post-D2) ;
- verrous du replay calibration : `t_free` (dur + cooldown), `ddd_min`,
  seuil de candidats −30 (sur-ensemble) ;
- **verrou « une quote = un trade »** : après un fill, l'arête est
  consommée jusqu'à sa prochaine cotation. Sans cela le même quote stale
  serait frappé en boucle — borne conservatrice (premier fill gagne,
  concurrents perdent) ; la mesure réelle de re-fillabilité revient à la
  phase detect IC.

## 3. Paramètres du générateur (hypothèses déclarées)

| Paramètre | Valeur | Justification |
|---|---|---|
| Vol latente | 6–9 %/an par devise | majors G10 réalistes |
| Facteur global | σ 5 %/an, β −0.2…+0.55 | corrélation risk-on/off |
| Sauts latents | Poisson 0.8/h/devise, σ ≈ 18 bp | repricing news/LP — seule source d'edge |
| Horloges tick | Poisson 0.8–12/s par paire | EURUSD rapide → croisés NZD lents |
| Spreads | 0.4–2.2 bp par paire, jitter lognormal | grille ECN plausible |
| Rafales spread | ~1.2/session, ×2.5–4, 5–20 s | élargissements news → cap |
| Volume | 1.0 nominal | pas de L1 dans le flux live → `vol_guard=False` |
| Paramètres moteur | Optuna #87 = défauts doc | fresh 500 ms, cap 1.7, lat 2.5 ms, τ 0.8 bp, q_min 0.5 |

Non modélisé (limitations déclarées) : profondeur L1, corrélation
intra-jour vol/spread, latence venue non constante, funding/swap (session
intraday sans overnight), impact du propre ordre sur la quote suivante.

## 4. Métriques (définies avant la campagne)

- `evals` : timestamps uniques évalués ; `pos` : évals `net > 0` ;
  `opp_per_meval` = pos/10⁶ évals.
- `candidates` : `net > −30` (sur-ensemble calibration) ;
  `send_rate` = trades/candidates.
- `consumed` : candidats bloqués par le verrou quote (rafale déjà arbitrée).
- `trades`, `aborts` (+ raisons), `wr` = P(pnl>0), `p_loss` = P(pnl<0).
- `pnl_mean`, `p05/p50/p95` par trade ; `sess_pnl_mean`, `sess_wr` par session.
- Sweeps appariés (mêmes graines) : latence {0.5, 2.5, 5, 10, 25} ms ;
  spreads ×{0.5, 1, 2}.

## 5. Résultats — seed 20261102, sessions de 30 min

### Contrôle de falsification

| Scénario | Sessions | Évals | pos>0 | Trades | WR | P&L |
|---|---|---|---|---|---|---|
| **sync** (état toujours cohérent) | 4 | 697 190 | **0** | 0 | — | 0 $ |

Aucune évaluation positive sur 697 k instants parfaitement cohérents :
le moteur ne fabrique pas d'edge ex nihilo. Le contrôle valide la
chaîne générateur + moteur (drapeau `falsified=false` dans le JSON).

### Scénario principal (async) — 48 sessions

| Métrique | Valeur |
|---|---|
| Évals | 8 384 199 |
| Instants `net > 0` | 3 278 (391 / 10⁶ évals) |
| Trades | 356 (~15/h) |
| Consommés (rafale déjà arbitrée) | 2 823 |
| Aborts | 1 (kill IOC) |
| WR par trade | 99,4 % — p_loss 0,3 % |
| P&L/trade | mean **+129 $**, p50 +91 $, p05 +3,7 $, p95 +360 $ |
| P&L/session | mean +960 $, 96 % de sessions > 0 |
| dur moyenne | 3,1 ms (≈1,3 jambe — les chemins courts gagnent) |

Note métrique : `send_rate` est dégénérée ici — le seuil candidats −30 $
rend quasi toute éval « candidate » ; l'entonnoir réel est
pos → consommé → trade (~11 % des instants positifs aboutissent, le
reste est la même fenêtre stale déjà arbitrée — verrou en action).

### Sensibilité latence (mêmes graines, 20 sessions)

| lat_ms | Trades | Aborts | WR | P&L/session |
|---|---|---|---|---|
| 0,5 | 128 | 0 | 100 % | +993 $ |
| 2,5 | 128 | 0 | 99,2 % | +989 $ |
| 5,0 | 127 | 1 | 98,4 % | +983 $ |
| 10,0 | 125 | 1 | 98,4 % | +959 $ |
| 25,0 | 121 | 6 | 92,6 % | +843 $ |

Plateau jusqu'à ~10 ms puis érosion progressive : la durée de vie d'une
quote stale (≈ prochain tick de la paire, ~80–800 ms) dépasse de loin la
latence d'infrastructure → **la latence n'est pas la contrainte liante**
à ces taux de tick ; la course sur la même quote (bornée par le verrou)
l'est. Cohérent avec la thèse : capture du lag de repricing LP, pas HFT
au microstructural.

### Sensibilité spread (mêmes graines, 20 sessions)

| ×spread | Trades | WR | P&L/trade | P&L/session |
|---|---|---|---|---|
| 0,5 | 147 | 100 % | +149 $ | +1 098 $ |
| 1,0 | 128 | 99,2 % | +155 $ | +989 $ |
| 2,0 | 126 | 100 % | +151 $ | +949 $ |

Sensibilité faible : l'edge (~18 bp de saut latent) domine les coûts
(spread + commissions ~1,5–4 bp même doublés). Le coût est de second
ordre à cette amplitude de désynchronisation — résultat honnête, pas un
artefact : il dit que si des fenêtres de cette taille existent, elles
sont exécutables avec marge.

## 6. Interprétation et portée — lecture honnête

**Ce que le MC démontre**
- La chaîne est falsifiable : cohérent ⇒ 0 edge ; désynchronisé ⇒ edge
  détecté et exécuté avec l'économie réelle (bid/ask, IOC, partiels,
  résiduel Convention A, commissions, abort par arête).
- Conditionnellement à l'existence d'une fenêtre stale, l'exécution est
  robustement rentable aux paramètres calibrés : +129 $/trade en
  médiane ~+91 $, et les seules pertes possibles sont les kills IOC
  (coût ≈ nul, fills non partiels avant kill).
- La marge de manœuvre latence est large : identique de 0,5 à 10 ms ;
  le goulot est la concurrence sur la quote, pas le tuyau.

**Ce que le MC ne peut PAS démontrer** (hypothèses déclarées)
- La fréquence réelle des fenêtres : λ_saut = 0,8/h/devise est une
  hypothèse — la phase detect IC la mesure directement (durée de vie,
  amplitude, fréquence des quotes désynchronisées).
- La victoire à la course : le verrou « une quote = un trade » suppose
  qu'on gagne chaque premier fill — en réalité concurrents et
  repricing-on-hit existent. C'est la borne optimiste.
- Les aborts micro-bruit : les quotes ne bougent qu'aux sauts ici ;
  les données réelles montrent une dérive continue qui tue τ_slip plus
  souvent — le MC sous-estime légèrement les aborts (impact faible, un
  kill coûte ~0 $).
- Profondeur > 100 k$, last-look/rejets LP, funding — hors modèle.

**Lien avec la suite** — le detect IC donnera : fréquence/amplitude des
désyncs réelles → remplace λ ; le demo-trade donnera : taux réel de
kill-IOC et coût d'unwind → valide ou corrige le modèle d'exécution.
