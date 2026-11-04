# Optuna sur corpus réel cTrader — études journal_ecn + journal_ecn_wide

**Corpus** : journaux cBot OgDetect réels — 1 975 126 ticks
(02/11 : 188 k · 03/11 : 937 k · 04/11 : 875 k à ~09h30 UTC).
Moteur Rust réel (`on_tick` + `exec_seq`), exec_seq = fills
séquentiels IOC + latence + liquidation Convention A.

## 1. Setup (volontairement minimal)

- Espace : `fresh_ms [200,2000]` · `spread_cap [1.2,3.0]` ·
  `ddd_min [1,6]` puis `[1,12]` (extension) · `tau_slip_bp [0.3,1.5]`
- Physique figée au scénario ECN : `lat 1 ms · comm 1.00 $/côté ·
  comm_scale 1.10 · q_min 0.5`, max_exec=400, objectif = PnL total
  réalisé, contrainte ≥30 execs.
- Étude 1 : `journal_ecn` — 200 trials TPE seed 7.
  Best +3 529,75 $ — `ddd_min` convergé vers la borne haute 6.0.
- Étude 2 : `journal_ecn_wide` — 60 trials TPE seed 11, `ddd_min`→12,
  seedée par top-10 de l'étude 1.

## 2. Verdict

| | Étude 1 (ddd≤6) | Étude 2 (ddd≤12) |
|---|---|---|
| Best PnL | +3 529,75 $ | **+4 059,82 $** (+15 %) |
| Params best | f859 c2.97 d5.97 t0.84 | **f985 c2.19 d11.80 t1.23** |
| Plateau top-6 | 3 527–3 530 $ | 4 040–4 060 $ |

**`ddd_min` veut sortir de l'espace** : optimum ~11.8, tous les top
trials entre 11.6 et 11.96 — le marché ne récompense que les grosses
dislocations (≥ ~12 $ brut). Sous-seuil = edge mangé par le risque
d'exécution. Cohérent avec la survie détecté→réalisé (15 % régime
volatil vs 95 % calme) : seuls les gros signaux survivent au bruit
de fill.

## 3. Test anti-overfit — split par jour (params du best)

| Jour | Épisodes | Execs | Total | Mean | Min | Max |
|---|---|---|---|---|---|---|
| 02/11 | 651 | 400 | +4 060 $ | +10.15 | −12.7 | +18.0 |
| 03/11 | 741 | 400 | +2 840 $ | +7.10 | −58.6 | +95.1 |
| 04/11 | 766 | 400 | +1 972 $ | +4.93 | −24.0 | +30.3 |

- **3/3 jours positifs** — le gain n'est pas porté par un jour chanceux.
- Structure saine : 650-770 épisodes/jour franchissent le seuil ~12 —
  la "grosse dislocation" n'est pas un événement rare, c'est le régime.
- Décroissance mean 10.1→7.1→4.9 $/trade sur 3 jours — edge qui
  s'érode (régimes différents ou venue qui reprice) : à surveiller.
- Reste in-sample (3 jours) ; **holdout réel = journal 05/11** non
  tuné — rejouer ces params dessus pour confirmation.

## 4. Params retenus (scénario ECN 1ms / 1$)

```
fresh_ms=985  spread_cap=2.19  ddd_min=11.8  tau_slip_bp=1.23
lat=1.0 ms    comm=1.00 $/côté comm_scale=1.10 q_min=0.5
```

⚠️ Caveats persistants : poll cBot ~1-2 Hz (borne optimiste fills,
pessimiste fréquence) ; lat=1 ms simulée ≠ mesurée (à valider UAT) ;
+4 060 $ in-sample ≠ espérance live — le holdout 05/11 tranchera.
