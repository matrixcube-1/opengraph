# 07 — Indicateurs de régime & seuil de déclenchement

Formules complètes et résultats vérifiés de la couche « régime »
(`regime_indicators/`) et de l'audit du seuil de déclenchement qui a mené
à `ddd_min = 2,5` (31 oct. 2026).

## 1. La porte de déclenchement — définition exacte

Pour un chemin π à l'instant `t` :

```
ddd_usd(π, t) = usd_out(π, t) − NOTIONAL_USD          (avant coûts)
net_usd(π, t) = ddd_usd(π, t) − comm_fx(π)            (après commission)

Décision : envoyer π* = argmax net_usd   ssi   net_usd(π*) ≥ ddd_min
```

- `ddd_min` est en **USD** sur notionnel ~100 K$ (pas en unités de spread).
- Verrous : `t_free = t + max(dur,1) + cooldown_s·1000` — un seul envoi à
  la fois, ré-évaluation à chaque candidat chronologique.
- Paramètres gelés : `fresh_ms 500 · spread_cap 1,7 · comm_scale 1,0 ·
  lat_ms 5,0 · tau_slip_bp 0,8 · q_min 0,5 · cooldown_s 0 · ddd_min 2,5`.

## 2. Audit du seuil — pourquoi `ddd_min = 0` saignait

Constat : le 22/10 produisait +48 393 $ (94,5 % wins) mais les 23/26/27
perdaient −628 / −59 / −61 $. Trois modèles comparés aux mêmes réglages :

| Modèle | 22/10 | 23/10 | 26/10 |
|---|---|---|---|
| Replay Python (`poc_engine`) | +1 225 577 $ | positif | +4 248 $ (97 %) |
| Rust `exec_seq` (internal, lat 5 ms) | +1 158 510 $ | +19 762 $ | +3 435 $ |
| **Venue NT** (`nt_realism --mode real`) | **+48 393 $** | **−628 $** | **−59 $** |

**Le saignement vit dans la couche venue**, invisible pour les deux
modèles rapides :

- IOC plafonné à la taille L1 affichée → partialisations,
- haircut de file : `h = spr·(hq_sp + kq·min(1, q/disp))`,
  `px_fill *= (1 − d·h)` — coût monotone dans la fraction consommée,
- latence réelle : l'ordre matche le livre du premier tick ≥ t+lat.

Distribution du net évalué au déclenchement : médiane **6,85 $** le 22,
**0,69 $** le 23, **0,86 $** le 26 — les jours calmes déclenchent des
distorsions sous-marginales < budget de friction (~1–2 $/exec), EV
négatif par construction. `ddd_min = 0` = « tout net positif déclenche ».

Biais d'objectif associé : `ΣPnL_jour` laisse le 22 (~98 % de la masse)
dominer → Optuna choisit `ddd_min = 0`. Correctif `poc_optuna_pen.py` :
contrainte hard `no_losing_day` (lexicographique). **Limite structurelle
découverte** : sur le modèle rapide tous les jours sont positifs — la
contrainte ne peut pas voir le problème ; le retune n'a de sens que sur
le flux venue réaliste.

## 3. Indicateurs de régime — formules

Crate `regime_indicators/` (sœur de `engine/`, en aval de l'oracle —
jamais de pruning des chemins). Référence de conception :
`conception_indicateurs_regime.pdf` + `verification_formules_*.pdf`.

### 3.1 Tension multi-sauts T̃

Sur le graphe complet K8, log-taux antisymétriques `x_ij = −x_ji` :

```
Résidu de Hodge :  R_ij = x_ij − (φ_i − φ_j),  φ optimal en moindres carrés
                   sur K8 : R_ij = (1/n)·Σ_k c_ijk   (moyenne des cycles
                   triangulaires par l'arête — incohérence exacte des mids)

Adjacence normalisée :  Ā_ij = |R_ij| / s̄_ij   (s̄ = demi-spread médian
                        glissant par arête), moyenne par ligne

Propagation multi-sauts :  M = Ā + Ā² + Ā³
   ⚠ les puissances comptent des MARCHES, pas des chemins simples —
   indicateur de régime seulement, jamais énumérateur de chemins ni DDD.

Dé-normalisation par s̄ → norme de Frobenius → T_raw(t)
Lissage : T̃ = EWMA_α(T_raw) ;  T_z = (T̃ − med) / MAD   (robuste, hist. W_T)
```

### 3.2 Dominance spectrale D / c⋆

Rendements des 8 paniers-devises sur fenêtre roulante :

```
D_t = σ₁² / Σ_k σ_k²   ∈ [0,1]   (part du facteur dominant)
c⋆  = devise de plus fort chargement |u₁| si > q_svdmin

API corrigée (rapport de vérif.) : SVD sur données CENTRÉES (p̂),
direction sur rendements BRUTS non centrés.
Cas test : choc USD pur → D = 1, c⋆ = USD.  Diffus → D ≈ 1/8.
```

### 3.3 Porte à hystérésis (implémentée, NON activée sur le déclenchement)

```
état initial : FERMÉ
fermeture immédiate si T̃ > θ_T  ou  D > θ_D
réouverture ssi T̃ < θ_T^-  ET  D < θ_D^-  maintenu ≥ hold
fail-closed : donnée périmée / non finie / absente → pas d'envoi
```

Décision d'architecture : la porte dure n'est **pas** branchée sur le
déclenchement — la haute tension correspond aux jours de récolte (le 22),
la fermer coûte l'alpha (coût d'opportunité documenté). Elle reste
disponible comme overlay de risque.

### 3.4 Modulation bornée (prête pour la couche sizing future)

```
g_π(T̃,D) = clip( exp[ −a_T·(T̃−t₀)⁺ − a_D·(D−d₀)⁺ ],  g_min, 1 )
```

Monotone non croissante en chaque argument, bornée — cible : bornes
Kelly, budget ES, plafond devise dominante (§12 du doc de conception).
**Non branchée** : la couche LP/HiGHS n'existe pas (voir §7).

## 4. Mesures sur les 4 jours (flux 1 s, ticks réels)

| Jour | T̃ médian | T̃ p99 | D médian | c⋆ dominant |
|---|---|---|---|---|
| 22/10 | **0,526** | 2,54 | 0,38 | JPY (40 %) |
| 23/10 | 0,280 | 0,36 | 0,41 | JPY (74 %) |
| 26/10 | 0,219 | 2,45 | 0,36 | JPY (57 %) |
| 27/10 | 0,197 | 1,21 | 0,35 | NZD/JPY (44 %) |

- **T̃ sépare le 22** (médiane ~2×) — descripteur de régime correct.
- **D ne sépare pas** : facteur JPY présent toute la semaine (rapatriement
  refuge — cohérent avec la macro observée).
- AUROC T̃→perte par exec ≈ **0,50** : T̃ qualifie le contexte, ne prédit
  pas le trade — comportement voulu.
- Le z-score intra-jour se rebaze chaque session : pour le régime de
  niveau « jour », c'est **T̃ brut** qui porte l'information ; T_z sert à
  détecter les ruptures intra-session.

## 5. Balayage du seuil sur le flux venue (exact)

Méthode `poc_thresh_grid.py` : re-sérialisation des 62 998 enregistrements
d'evals du run venue officiel — verrous `t_free`/gate identiques.
Validation : `ddd=0` reproduit le baseline **au centime**
(48 392,94 / −627,97 / −58,57). Biais connu conservateur : les execs
débloquées par un seuil plus haut sont invisibles → borne inférieure.

| ddd_min | Total | Pire jour | Jours <0 | Execs |
|---|---|---|---|---|
| 0 | 47 706 | −628 | 2 | 11 266 |
| 1,0 | 49 176 | +57 | 0 | 10 313 |
| **2,0–2,5** | **49 598–49 615** | +51–56 | 0 | ~9 750 |
| 3,0–4,0 | 49 600–49 318 | +36–44 | 0 | ~9 500 |
| ≥6 | décroît | +15–28 | 0 | chute |

Seuil adaptatif `ddd_min_t = clip(hi − k·T̃_t, lo, hi)` : même plateau
(49,2 k$ max), pas de gain mesurable sur 3 jours → **retenu : statique
2,5 $** ; T̃ reste en télémétrie/overlay de risque. Cooldown >0 destructeur
(−50 % à 10 s) → `cooldown_s = 0`.

## 6. Verdict holdout 27/10 (post-hoc, `holdout_ddd25.json`)

| Config | Execs | PnL | Win rate |
|---|---|---|---|
| `ddd_min = 0` (officiel, aveugle) | 134 | −60,65 $ | 34,3 % |
| **`ddd_min = 2,5`** | 6 | **+11,25 $** | **83,3 %** |

Rerun venue complet — la re-sérialisation avait prédit +11,25/6 execs au
centime. Le seuil supprime 128 execs de bruit, conserve 5/6 gagnantes.
⚠ Le 27/10 n'est **plus aveugle** pour cette famille de configs ; 6 execs
= échantillon mince ; robustesse du plateau à confirmer sur plus de
sessions.

## 7. Non câblé (documenté, hors scope actuel)

- **HiGHS / Kelly / ES** : exige `μ_USD`, `σ²_USD` par chemin — procédure
  d'estimation non spécifiée ; μ<0 → LP infaisable ; σ²→0 → explosion.
  La modulation `g_π` est prête à s'appliquer sur les seconds membres.
- **Hawkes MLE / p_persist** : sémantique d'événement/résorption non
  finalisée ; épisodes exportés (`cand.npz`), MLE à écrire.
- **Porte à hystérésis** : implémentée + testée (43 tests crate), non
  activée sur le déclenchement.
