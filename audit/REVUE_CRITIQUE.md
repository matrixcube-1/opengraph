# Revue critique — Corpus Arbitrage FX à chemins ouverts
**Audit indépendant — 25 octobre 2026**

Documents revus : `Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf` (79 p.), `Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf` (69 p.), `Disponibilite_Ticks_Dukascopy.pdf` (6 p.), `Simulation_MonteCarlo_Chemins_Ouverts.pdf` (6 p.).

Méthode : lecture intégrale des 4 documents + **vérifications indépendantes** (énumération DFS des dénombrements re-codée de zéro, recalcul des formules, spot-checks croisés entre documents). Deux passes d'audit parallèles (corps normatif / annexes et données) fusionnées ici. Le script de vérification des dénombrements est joint : `audit/verify_counts.py`.

---

## Verdict global

Corpus d'une **rigueur et d'une traçabilité exceptionnelles** pour le genre : les dénombrements, la quasi-totalité des formules et les corrections d'audit sont **exactes et vérifiables**. Les défauts se concentrent à trois endroits où le document est le moins audité :

1. **L'économique du résiduel** — la règle d'envoi compare une marque au mid à des coûts d'entrée seuls : ni le coût de sortie du résiduel, ni l'atomicité de l'exécution multi-jambes, ni la fraîcheur des quotes ne sont modélisés. Le break-even réel est structurellement plus haut que le 21,93–8,07 canonique.
2. **La chaîne de calibration** — Dukascopy (proxy feed, échantillon M5) ne peut pas calibrer des paramètres propres au flux IC Markets ; le plan Optuna n'existe que dans le Scellé, absent de la « référence unique » ; la venue CME n'a aucune source de données.
3. **La preuve Monte Carlo** — valide la transcription des formules (tautologies non falsifiables, faux positifs jamais testés), pas leur pertinence économique.

---

## I. Ce qui est vérifié et solide

| Élément | Vérification |
|---|---|
| Dénombrements (12 cas : 4 672 / 19 792 / 3 112 / 3 632 / 15 392 / 10 912 / 46 192 / 12 472 / 52 792 / 16 072 / 71 512 + invariance répartition) | **11/11 reproduits par énumération indépendante** (`audit/verify_counts.py`), détail par graphe conforme (EUR 521, USD 485…), forme close identique |
| Coefficients A(3)=259, B(3)=37, Λ(3)=50 ; A(4)=1099, B(4)=157, Λ(4)=260 | Recalculés exacts |
| Coût canonique OTC 3×(3,50+3,81)=21,93 ; seuil tier S = 30−21,93 = 8,07 ; B−A = 3×3,81=11,43 ; BIR exp(−0,5)=0,6065 ; p_h(1j)=5,53e-5 | Exact |
| Contre-exemple Dijkstra (optimum 4 vs 3), Hawkins α/β<1, ES Rockafellar-Uryasev, Kelly redimensionné (C-5), exemples PDS 33/33 revendiqués | Corrects ; corrections d'audit v2.1 (C-1 à C-6) sont de vraies améliorations |
| Sémantique « ≤3 jambes de change + bout hors budget » (chemins à bout = jusqu'à 4 arêtes/fills) | Confirmée cohérente partout ; **à expliciter en tête de doc** |

Honnêteté documentaire remarquable : « Valide avec réserves », points ouverts D-1→D-17, marques À CALIBRER, limites §19.2, coquilles des sources documentées.

---

## II. Findings CRITIQUES (bloquants avant toute production)

### C1. Coût de sortie du résiduel absent de la règle d'envoi — marque mid non réalisable
- `DDD = S × P_arrivée − P_départ` avec **P = mid de marquage** (§7, Scellé l.2032-2035 ; Const. l.2422-2424) alors que les jambes sont aux prix exécutables. Le résiduel est marqué à un prix **non exécutable** : chaque arrivée est gonflée de ≈ ½-spread de sa devise/paire (~0,5–4 USD/100k).
- `Coût_total` (§8.3) = entrée + portage + base + conversion + div/roll — **aucune provision de sortie** (spread + frais + impact de la clôture du résiduel). Le seul aveu : D-10 (coûts de sortie manquants de l'optimal stopping), jamais reporté sur la règle d'envoi.
- Conséquence : « ENVOYER ⇔ DDD_eff > Coût_total » compare une marque mid à des coûts d'entrée. Le vrai break-even ≈ **+1 jambe de coût** (~7,3 USD) + ½-spread de marquage — soit quasiment tout le net canonique 8,07 USD.
- Tension interne assumée : « Le DDD initial est **réalisé** : prime d'illiquidité **captée** » (§1) vs « Le DDD n'est pas du cash ; c'est une marque de position ouverte » (§7).
- **Fix** : provision de sortie dans Coût_total (ou marquage du résiduel au prix de sortie exécutable), et explicitation du modèle de clôture/recyclage du résiduel en départ du chemin suivant.

### C2. Atomicité / risque de jambe non modélisé
- « Exécution quasi simultanée » posée (§1) mais **aucune des 38 règles** ne borne l'écart entre fills, ne traite le fill partiel/rejet (requote, last-look) ni ne spécifie un repli (unwind, timeout, hedge d'urgence).
- S(h+latence) modélise la survie du *déséquilibre*, pas l'exécution *par fill*. Sur IC Markets, 3-4 ordres séparés via API retail = risque de jambe réel ; au CME, un « triangle logique » = 4 fills (F10 le sait pour le budget, pas pour le risque).
- Corollaire : **pas de fraîcheur par jambe** dans la règle d'envoi — des ticks asynchrones peuvent fabriquer un DDD fantôme (classe de bugs connue, cf. expérience Spread Arbitrage de l'utilisateur).

### C3. Double-comptages résiduels dans la règle d'envoi (classe F06 — prétendue éliminée)
1. **Impact compté deux fois** : `DDD_eff = DDD_th − ησQ√L` (§7.2) **et** `Coût_total` inclut `Glissement` défini explicitement comme `Impact ησQ√L` (Tableau 8.4 ; §17 « régression du glissement sur Q×√L »). Le pseudo-code reproduit le bug (`ddd_exec` soustrait l'impact, `cost_A` ajoute `slippage`). Net faussé de **2×ησQ√L**.
2. **Spread du bout compté deux fois en Convention A** : `Coût_bout` (spread CFD) est dans Coût_total **inconditionnellement** (§8.3 ; seul `Spread_v(j)` des jambes FX est marqué conv. B), alors que « le DDD d'un bout CFD inclut le spread » (§7.1) et que Tableau 8.12 l'admet (« en Convention A le spread est dans le DDD »).
3. Ambiguïté associée : `r(e)` défini « taux exécutable **net de frais** » (§0) — si littéral, les commissions du Coût_total sont double-comptées ; à écrire « exécutable bid/ask hors commissions ».

### C4. Chaîne de calibration invalide en l'état
- **Proxy venue** : Dukascopy = feed ECN ≠ prix exécutables IC Markets Raw. Ce qui ne transfère pas : spreads exécutables, fréquence/durée des incohérences internes de cotation (les événements DDD>coût sont propres au moteur du *broker*), slippage, latence — soit **exactement les paramètres du §17**. Le Scellé le concède (J.5) mais minimise ; le doc Dukascopy standalone se lit comme un feu vert sans la mise en garde.
- **Résolution** : l'Annexe J prévoit un échantillon principal en **barres M5 OHLC** — insuffisant pour une décision ~50 ms. **Partiellement mitigé** : `dukascopy-node -t tick` fournit bid/ask + volumes à l'horodatage milliseconde (confirmé doc officielle), donc Hawkes/S(h), le spread cap et un pseudo-BIR (V_bid−V_ask)/(V_bid+V_ask) sont calibrables sur ticks — à condition que le plan Optuna pivote sur le tick (des dizaines de Go ; protocole de synchronisation inter-symboles toujours à spécifier ; volumes = lots indicatifs, pas profondeur réelle).
- **Synchronisation** : reconstituer des états simultanés exécutables sur 28 paires + bouts depuis des fichiers .bi5 asynchrones par symbole = protocole d'alignement non spécifié → risque de désalignements artificiels (faux signaux).
- **CME** : aucune source (Databento/DataMine cités sans audit) → la moitié du dual-venue n'est pas calibrable ; « optimisation **conjointe** » (§17 Scellé) est trompeuse.
- **Existence ≠ disponibilité** : jugée sur catalogue `startHourForTicks` ; sonde live bi5 non conclusive (503/timeout) — honnêtement déclaré, mais « oui » = assertion de catalogue.
- **Fix** : échantillon de quotes réelles IC Markets (même court, via API) pour étalonner le mapping Dukascopy→IC ; traiter les seuils Optuna comme priorisation, pas calibration.

### C5. La simulation Monte Carlo ne prouve pas ce qu'elle prétend
- **Tautologies** : 11 « contrôles analytiques » invérifiables-faux-par-construction (Score≡DDD = même expression ; « 30>21,93→True » teste `>` ; « identité exacte » code vs lui-même). Verdict « OK » sur-interprété : le titre dit « validation des formules », les limites admettent « application des formules ».
- **Faux positifs jamais testés** : send rate 0,000 % hors `edge_injected` — soit le bruit préserve la cohérence triangulaire, soit les DDD n'atteignent jamais le coût ; dans les deux cas le filtre n'est éprouvé que sur des edges **injectés** (circulaire).
- `P(net<0|sent)=0,00 %` est mécanique (un chemin envoyé a par construction DDD>coût sur les mêmes prix). `Mean net (tous) = −4,20` = moyenne contrefactuelle du meilleur chemin, envoyé ou non — **non défini, présenté comme un P&L**.
- `chemins = 3 108` vs 3 112 attendu (−4 arrivées directes départ→bout, 0 jambe de change) — convention défendable mais **non documentée et différente** du dénombrement scellé.
- Écart send rate A (0,5667 %) vs B (0,6083 %) ~7 % relatif non expliqué → suggère asymétrie demi-spread/spread complet dans le code — **précisément le piège F06** ; le test d'équivalence A↔B aurait dû être LE contrôle.
- p_an ≈ 2 % implicite (contrôle) vs 1 % dans l'exemple documenté.
- Manquent : risque de jambe, latence, coût de sortie, granularité CME, Hawkes dynamique (λ constant), filtrage effectif de p_persist.

---

## III. Findings MAJEURS (cohérence documentaire / méthode)

### M1. Conflit d'autorité et dérivation non déclarée du Scellé
- Deux « références » concurrentes : Constitution « référence unique » vs Scellé « référence autonome », même date, contenu divergent.
- **Preuve de dérivation** : « Fin de la Constitution méthodologique unifiée » en dernière ligne du Scellé ; phrases corrompues par suppression des marqueurs de version (« La posait un univers de 27 devises », « la, et donc la, interdisent ») ; références d'annexes périmées ; **codes d'arbitrage C15–C31 cités ~12 fois mais définis nulle part** dans le Scellé (l'annexe C des arbitrages a été supprimée).
- **L'Annexe J (Dukascopy/Optuna) et la ligne §17 « optimisation conjointe » n'existent que dans le Scellé** — 0 occurrence dans la Constitution. Le plan de calibration n'est pas versionné.
- Contradiction interne créée : §10.3/§15.2 exigent « tick-by-tick des carnets CME et flux IC Markets » alors que l'Annexe J substitue Dukascopy.
- **Fix** : promouvoir le Scellé en v2.3 versionné de la Constitution (avec changelog) ou le retirer ; intégrer l'Annexe J.

### M2. Formule normative erronée : facteur FX parasite dans coût_bout et P_S
- `coût_bout = Spread × (100 000/P_niveau) × FX` et `P_S = Spread × 100 000 × FX / 8,07` (§2.3, §8.7, Annexe A) — le FX **s'annule** dimensionnellement pour un notionnel fixe de 100k USD : coût = `Spread × 100 000/P_niveau`, sans FX.
- **Toutes les valeurs machine (Annexes H/J) utilisent la forme correcte sans FX** (DE40 4,17 ; JP225 10,42 ; NETH25 21,74 ; seuils US500 4 957…) — la formule écrite est fausse ; un implémenteur la suivant effondre les tiers hors USD (JP225 → 0,07 USD). Inconsistance aussi avec `Coût = (Ask−Bid)/P_mid × 100 000` (correcte) du même §8.7.

### M3. Fenêtre d'annonce « 10 s » non opérationnalisable
- Source non définie (calendrier ? flux news ?), périmètre (devise concernée ? toutes ?), sens (avant/après ?) ; 10 s insuffisant pour un repricing macro (NFP, CPI). La clause « refermeture du spread » fait le vrai travail mais la règle est invérifiable/implémentable telle quelle (R9, §8.5, §17 « Fixe »).

### M4. Hawkes : événements et sémantique de résorption mal définis
- « Chocs » = variation de **mid** > 2σ/30 s (§10.3) vs « chocs **DDD** » (§17) — pas le même processus ponctuel ; un choc de mid peut créer autant que résorber un déséquilibre.
- **S(h) porte deux rôles incompatibles** : survie de l'edge jusqu'à l'exécution (on veut haut) et rythme de résorption post-entrée (résorption = profit). « Un S(h) élevé justifie de conserver » — or un déséquilibre qui persiste n'est pas un déséquilibre qui paiera ; la **direction** de la résorption n'est jamais modélisée.
- **Ĥ > 0,55 aveugle au signe** : porte dure sur estimateur R/S instable, sans zone tampon ; la persistance du mid de l'arrivée peut dériver *contre* le résiduel — la logique voudrait la mean-reversion de l'**écart**, pas du prix d'arrivée.
- `p_persist` est un **score**, pas une probabilité (produit hétérogène). BIR défini **par jambe** mais exigé par chemin — agrégation non spécifiée.
- `h` polysémique : horizon d'exécution (+50 %, §17) vs horizon de détention (§14.1, p_h, portage) vs horizon de résorption (§8.5) — trois quantités différentes, un seul symbole.

### M5. Structure de la règle d'envoi incomplète
- Présentée biconditionnelle « ENVOYER(π) ⇔ … » mais **omet la condition nécessaire p_persist > seuil** (règle 22 / pipeline) — l'iff est faux ou le périmètre doit être délimité.
- `niveau de marge ≥ ρ_min` référence l'état **post-dimensionnement** (circularité, résolue par recheck_send — formulation en avance de phase).
- γ = 10 **inerte sur l'univers réel** : pour spread ≤ 0,5 %, atténuation < 5 % (< 1 % en FX) — dimensionné pour des exotiques exclues.
- Spread cap 1,7× : dérivé d'un **point unique** (edge 30, 3 jambes, spreads uniformes) et partiellement redondant avec le test de coût en conv. A (le spread courant est déjà dans le DDD) — garde-fou à justifier.
- « ~5 USD » d'élimination CFD non dérivé ; seuils de tier arrimés à 21,93 alors que le coût de production ≈ 23,43 (marge réelle ≈ 6,57) ; XAGUSD mesuré 7,29 ≤ 8,07 mais tier L (critère violé, couvert par le « tier ordinal »).

### M6. Viabilité économique non établie
- **Ancre « edge de référence 30 USD » non sourcée** — dont dépendent le cap 1,7×, les tiers S/L/X, toutes les illustrations P&L. Si les désalignements G10 réels >3 pips à ~50 ms sont rares, le send rate de production ≈ 0 — la fréquence attendue n'est jamais estimée, ni le seuil de rentabilité vs coûts d'infrastructure.
- **Kelly/HiGHS nécessite μ_USD, σ_USD par chemin** — procédure d'estimation de μ (rendement de résorption) jamais spécifiée ; bruit d'estimation ≫ signal (~8–16 USD/100k). `x_max < 0` si μ < 0 → LP infaisable (pas de clamp) ; σ²→0 → explosion.
- LGD × Notionnel surestime l'exposition au défaut (créance = gains latents + marge déposée, pas le notionnel) — conservateur, matérialité faible.
- Pas de **stop par trade** sur le résiduel (volatilité de la position ≫ edge) — seul un stop global agrégé ; sortie confiée à un optimal stopping déclaré « non implémentable » (D-10).
- Corrélation des chemins via **jambes intermédiaires partagées** non bornée (Herfindahl ne porte que sur les arrivées).

---

## IV. Findings MINEURS / INFO

| # | Finding | Où |
|---|---|---|
| i1 | Règle 1 « Dix CFD » obsolète vs règle 28 « 34 bouts » | Const. l.3495 vs l.3607 |
| i2 | §5.4 « activation v2.1 » vs « v2.3 » partout ailleurs | l.2274 vs l.52, l.4273 |
| i3 | Annexe A : règle d'envoi et Coût_total incomplets vs v2.2 (manque rollover, séance, ρ_min, conv/div/roll) | l.4420-4425 |
| i4 | « 17:00 NY = 00:00 serveur **toute l'année** » faux ~4 sem/ans (DST US ≠ UE) — décale le cutoff de financement | §11.1 l.3056 |
| i5 | Pseudo-code : rollover non testé pour chemins purement FX (rule 32 violable) ; commission 3,50 hardcodée (compte USD seulement) ; conv. B non implémentée ; contrepartie limitée aux bouts vs §14.1 (résiduels FX) ; `DDD_eff,CFD>0` implicite seulement | Annexe F |
| i6 | « DP sur (nœud, k) » n'impose pas la simplicité des chemins (état requis : nœud, masque visités, k) — l'énumération reste l'oracle | §6.1, règle 19 |
| i7 | Borne S=0,30 : « < » (§11) vs « > » (règle) vs « ≤ » (code) | l.3048 vs l.2709, l.5333 |
| i8 | `p_h = 1 − (1 − p_an)h` **sans exposant** (2 occ.) ; « e−λh » ; net 4 jambes 11,78 vs 11,74 (arrondi, admis) | Scellé l.4573, l.4145, l.2570, T.8.6 |
| i9 | Règle 37 « plafond de messages » = « À fixer » — invérifiable ; « 17 incohérences » = 17 arbitrages dont ~9 décisions | §17 ; §18.5 |
| i10 | Kelly : formule `x_max = κ·W₀·N_ref·μ_ref/σ²_ref` OK ; mais pas de clamp μ<0 ni garde σ²→0 | §12 |
| i11 | 4e jambe « marginale CME » (+11,78 net, 99,4 %) et « nulle OTC » (+0,76) — caractérisation approximative ; choix politique, pas calcul | T.8.3, T.8.6, règle 4 |
| i12 | Variante impact η′σ√(Q/V_day) : unité (coût relatif vs USD) non spécifiée | §12 |
| i13 | S(h) = exp(−∫λ) avec λ Hawkes stochastique : approximation (intensité prévisionnelle) non discutée | §10.1 |
| i14 | XAUJPY < 4 ans — traité par restriction de fenêtre mais crée une asymétrie d'échantillon Optuna | Annexe J |
| i15 | Survivorship catalogue ; volumes .bi5 (pseudo-BIR) inexploités ; qualité par séance non auditée | Dukascopy |
| i16 | « Graphe G10 » pour 8 devises ; datation uniforme « Octobre 2026 » toutes versions | Dukascopy p.1 ; T.B.1 |
| i17 | R10 « max-de-bruit ≠ signal » : juste mais non opérationnalisable (pas de critère quantitatif) | §16 |

---

## V. Recommandations prioritaires

1. **Corriger la comptabilité du résiduel** (C1) : provision de sortie dans Coût_total ou marquage au prix de sortie ; recalculer le seuil de rentabilité réel ; documenter si le résiduel est recyclé en départ du chemin suivant (vue portefeuille) — c'est le trou économique principal.
2. **Modéliser l'exécution** (C2) : borne de fraîcheur par jambe dans la règle d'envoi ; politique de fill partiel (unwind immédiat + coût provisionné) ; recalcul du DDD entre fills ; timeout d'exécution.
3. **Corriger les double-comptages** (C3) : choisir si l'impact vit dans DDD_eff *ou* dans Coût_total (recommandé : DDD_eff, avec glissement = résidu mesuré, pas la même formule) ; exclure le spread du bout de Coût_bout en Convention A ; réécrire la définition de r(e).
4. **Corriger les formules de coût de bout** (M2) : supprimer le facteur FX parasite dans `coût_bout` et `P_S` (les valeurs machine sont déjà bonnes — aligner la formule).
5. **Trancher l'autorité documentaire** (M1) : Scellé → v2.3 versionné ou retrait ; intégrer l'Annexe J ; purger les références pendantes (C15–C31, annexes renommées, phrases corrompues).
6. **Refonder la calibration** (C4) : ticks IC Markets réels pour étalonner Dukascopy→IC ; M5 insuffisant pour les paramètres d'exécution — réserver M5 à la priorisation ; source CME séparée ; protocole de synchronisation des quotes.
7. **Rendre la MC falsifiable** (C5) : générateur avec incohérences temporelles et bruit non triangulairement cohérent ; test d'équivalence A↔B ; coût de sortie ; définir « Mean net » ; documenter les 3 108.
8. **Définir les manquants** (M3–M6) : source/périmètre de la fenêtre d'annonce ; événement Hawkes (résorption vs mid) ; estimation de μ pour Kelly ; stop par trade ; procédures de calibration pour les paramètres « Fixe ».
9. **Non vérifiable hors périmètre** : les scripts (`verify_path_counts_v22.py`, `run_mc.py`, `costs_checks_v22.py`), sorties CSV/JSON et sources S1–S4 ne sont pas dans le dossier — les verdicts « 12/12 », « 33/33 », « 110 contrôles » reposent sur les journaux reproduits dans les PDF ; une re-exécution indépendante est le contrôle manquant (mes dénombrements confirment cependant les 12 cas).

---

## Annexe 0 — Inventaire des formules (Annexe A de la Constitution, ~42)

~40/42 exactes ; 1 erreur normative (facteur FX parasite dans `coût_bout` / `P_S` — #34, finding M2) ; le reste des anomalies est dans l'assemblage (double-comptages C3, sortie absente C1) plutôt que dans les formules individuelles. Les patchs v2.1 (C-1 à C-6, issus de l'audit 45 formules : 35 OK / 5 ambigu / 4 erreur / 1 incomplet) sont tous correctement appliqués.

- **OK vérifiés par recalcul** : Solde ; DDD général et unitaire ; Score ; Bellman-Ford ; dénombrements (11/11 reproduits, dont N_g par graphe — EUR U1-N = 1265) ; Hawkes α/β<1 ; S(h) = e^{−∫λ} ; Hurst R/S ; BIR_aj γ=10 ; Kelly post-C-5 (dimensionnel) ; impact ησQ√L (forme) ; objectif HiGHS LP/QP ; ES RU ; p_persist borné ; p_h = 1−(1−p_an)^(h/1an) → 5,5348e-5 à 2 %/jour ✓ ; DDD_eff,CFD → 28,62 à LGD=25 % implicite ✓ ; coût OTC 21,93 ; CME 1,89+4,55 ; commission normalisée 3,50/P_base → moyenne 4,00/28 paires ; coût spread bout (Ask−Bid)/P_mid×100k ; swap/financement par classe ; provision portage ; Ajust_roll ; Ajust_div ; Provision_conv ; marges ; écart CFD/future + F_th = I·e^{(r−q)T} ; écart_rel métal-croisé.
- **ERREUR normative** : `coût_bout = Spread×(100 000/P)×FX` et `P_S = Spread×100 000×FX/8,07` — le FX s'annule pour 100k USD de notionnel ; toutes les valeurs machine utilisent la forme sans FX.
- **OK avec réserves** : DDD (P=mid non exécutable) ; BIR_aj (inerte <5 % sur l'univers réel) ; Kelly (pas de clamp μ<0, pas de source de μ) ; p_persist (score, pas proba ; agrégation BIR non spécifiée) ; Coût_total (double-comptages + sortie absente) ; règle d'envoi (iff incomplète : manque p_persist/rollover/séance/ρ_min) ; optimal stopping (schématique, D-10) ; S(h) (approx. intensité prévisionnelle non discutée).

## Annexe — Vérifications indépendantes effectuées

- `audit/verify_counts.py` : énumération DFS indépendante (chemins simples ≤L sur K8 + bouts puits + feuilles terminales) — **11/11 configurations reproduites** + détail par graphe ; la 12e configuration (invariance de répartition) suit trivialement de la forme close.
- Recalculs : 21,93 ; 8,07 ; 11,43 ; exp(−0,5) ; p_h(1j)=5,5348e-5 ; 28,62 (LGD=25 % implicite) ; coûts des bouts (sans FX) ; 3108 = 3112−4.
- Spot-checks : 0 occurrence « Optuna/Dukascopy » dans la Constitution (confirmé) ; artefacts de corruption du Scellé (confirmés : « Fin de la Constitution », phrases « La posait… ») ; « Glissement = Impact ησQ√L » (Tableau 8.4) et DDD_eff = DDD_th − ησQ√L (§7.2) — double-comptage confirmé ; `Coût_bout` non marqué conv. B (§8.3) — confirmé.
