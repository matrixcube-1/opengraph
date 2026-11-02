# Journal des corrections — 25 octobre 2026

Périmètre : **erreurs confirmées uniquement** (choix utilisateur), appliquées aux **deux** documents.
Méthode : redaction PyMuPDF (fond blanc) + réinsertion en Arial, après backups dans `backup_pdf/`.
Vérification : ré-extraction texte de chaque page modifiée + rendus visuels (`audit/v_*.png`).

## Constitution v2.2 (79 pages)

| Page | Correction | Avant → Après |
|------|-----------|----------------|
| 9 | r(e) glossaire | « taux exécutable net de frais » → « taux exécutable bid/ask brut » |
| 12 | Facteur FX parasite | `coût_bout = Spread × (100 000 / P_niveau) × FX(de‐vise→USD)` → `… × (100 000 / P_niveau)` |
| 22 | §5.4 version périmée | « requiert v2.1 (…) » → « requiert une version dédiée (v2.3 ou ultérieure, §19) » |
| 23 | r(e) §6.1/§6.2 | « taux effectif après frais » → « taux exécutable (bid/ask) » ; « net de frais » → « bid/ask brut » |
| 27 | T8.4 cellule Glissement | « Impact ησQ√L … dépend de la taille par jambe Q. » → « Glissement résiduel (écart fill vs quote) ; l'impact ησQ√L est déjà déduit dans DDD_eff (§12) — ne pas le recompter ici. » |
| 29 | §8.3 coût_bout | « (spread CFD / tick + frais du future) » → « (conv. B : spread + frais future ; conv. A : frais seuls — spread dans DDD) » |
| 29 | §8.3 note * | « * Spread_v(j) n'est inclus qu'en Convention B (§8.4). » → « * Spread_v(j) : conv. B seulement ; Glissement_v(j) : résiduel mesuré (écart fill/quote) — l'impact ησQ√L est déjà dans DDD_eff (§12). » |
| 31 | Seuil tier + P_S | `(100 000 × FX)` → `(100 000)` ; `P_S = Spread × 100 000 × FX / 8,07` → `P_S = Spread × 100 000 / 8,07` ; cellule 8.12 `P_niveau × FX → USD` → `P_niveau → USD` |
| 36 | Borne survie | `S(h + latence) < 0,30` → `≤ 0,30` (cohérent avec le gate `> 0,30`) |
| 44 | Règle 1 | « Dix CFD collés » → « Trente-quatre bouts collés » |
| 55 | Annexe A P_S | `× FX / 8,07` → `/ 8,07` |
| 65 | Pseudo-code | « net de frais ; » → « hors commissions ; » ; « 10 CFD collés (IC) » → « 34 bouts collés (IC) » ; commentaire enum → « // CFD IC (34 bouts v2.2, §2.3) » |

## Document scellé (69 pages)

Corrections parallèles : facteur FX (p.7, 25, 49), glissement T8.4 (p.21), convention A/B coût_bout + note (p.23), r(e) (p.4, 18, 54), borne `≤ 0,30` (p.30), « Trente-quatre bouts collés » (p.38), pseudo-code 34 bouts (p.54), « Fin du Document scellé » (p.69, était « Fin de la Constitution… »).

Corruptions réparées (marqueurs de version strippés, texte source = Constitution) :

| Page | Correction |
|------|-----------|
| 6 | « apparaissent **en** comme devises » → « apparaissent comme devises » ; « **La** posait un cadre » → « **Le socle** posait un cadre » |
| 7 | « **La** applique … de la, » → « La v2.2 applique à ce catalogue les règles topologiques de la v2.1, sans les modifier : » ; « Statut : (dénombrées… » → « Statut : FRONTIÈRE (dénombrées… » |
| 11 | Cellule Bouts : « : 10 CFD … ; : 34 bouts » → « **v2.1** : 10 CFD … ; **v2.2** : 34 bouts » |
| 14 | « 8 devises seules (, vérifié) » → « (v1.0, vérifié) » |
| 18 | « max-balance () » → « (v1.3/v1.4) » |
| 20 | « (audit :). » → « (audit : OK). » |
| 21 | « par lot (, §7, §9.3) » → « (v1.0, v1.1 §7, v1.3 §9.3) » |
| 23 | « h ; : par classe » → « h ; v2.2 : par classe » ; « rollover IC Markets (: 23:55 » → « (v2.2 : 23:55 » ; « ρ_min (, §12.1) » → « (v2.2, §12.1) » ; « Illustration (), Convention B » → « (v1.1 / v1.3), Convention B » ; « (: « le carnet » → « (v1.0 : « le carnet » ; « » ; : 0,73 » → « » ; v1.3 : 0,73 » ; « La (règle 7) » → « La v1.1 (règle 7) » ; « la (« DDD » → « la v1.3 (« DDD » ; « La qualifiait » → « La v1.4 qualifiait » ; « La, et donc la, » → « La v1.4.1, et donc la v2.0, » |
| 24 | « (: −21,88, valeur » → « (v1.1 : −21,88, valeur » ; « par la, F05). » → « par la v1.4.1, F05). » ; « (: +8,09) » → « (v1.1 : +8,09) » ; « ( simulé : −21,88) » → « (v1.1 simulé : −21,88) » ; « ( §8). Les » → « (v1.1 §8). Les » ; « ( §8, simulation). La » → « (v1.1 §8, simulation). La » |
| 28 | « Calibration (, par venue depuis ) » → « (v1.3, par venue depuis v1.4) » |
| 32 | « (C15). » → « (C15, cf. Annexe C Const. v2.2). » |
| 33 | « (audit :). » → « (audit : OK). » |
| 44 | « (Annexe C, C15–C31), » → « (Annexe C de la Const. v2.2, C15–C31), » |
| 49 | Tableau A.1, colonne Origine : « / » → « v1.0 / v1.3 » ; 14 × « — » → « v2.2 » |
| 50 | D-2 : « : le carnet » → « v1.0 : le carnet » ; « » ; : 0,73 » → « » ; v1.3 : » ; « jambe ; : un tick » → « jambe ; v1.1 : un tick » (+ restauration du « ≈ ») |
| 56 | « S(h) (: « × facteur H ») » → « (v1.0 : « × facteur H ») » |

## Limites connues

- Les zones réinsérées sont en Arial (vs Noto Sans embarqué) et parfois en taille réduite ; les cadres de redaction laissent des fonds blancs visibles. Contenu correct, aspect patché.
- Non corrigé (hors périmètre « erreurs confirmées ») : coût de sortie du résiduel absent de la règle d'envoi, risque d'exécution non atomique (fraîcheur/fills partiels), « Alias »/« Rôle » sans suffixe de version p.7 (stylistique), autres marqueurs de version retirés là où le texte reste grammatical.
- Codes C15–C31 du Scellé : renvoyés à l'Annexe C de la Constitution (non définis dans le Scellé par construction).

---

# Round 2 — Tableau de vérification en 10 points (Constitution v2.2)

Corrections appliquées aux deux PDF (Constitution + Document scellé) d'après le tableau de vérification fourni (écarts vérifiés dans le texte ou contre la PDS IC Markets 28/03/2021). Scripts : `apply_corrections_r2.py`, `fix_blanks_r2.py`. Sauvegardes : `*.pre_r2.pdf`.

| N° | Correction | Où |
|----|-----------|-----|
| 1 | Dividende indice : « long débité, court crédité » → « long crédité, court débité » (aligné PDS §5.3) | Const. Annexe A p.55 + §8.8 ; Scellé p.49 |
| 2 | `Ajust_div_USD = ΔI × valeur_du_point`, signe crédit long / débit court, compté une seule fois (coût ou DDD) | Const. p.32 ; Scellé p.25 |
| 3 | `p_persist` : seuil 0,40 non à l'échelle du produit → « seuil τ_persist : échelle unique — S(h) seul ou produit recalibré » ; ex. G.3 annoté (BIR_aj ≥ 0,51) | Const. p.34, §10.4, G.3 p.67 ; Scellé §10.4, G.3 p.56 |
| 4 | Financement indices : `d = 360` pour l'exemple PDS §11.2 ; « 365 UK/AU » conservé comme écart documenté « À VÉRIFIER » (Annexe I fidèle à S3) | Const. p.3, p.17, p.70 ; Scellé |
| 5 | C28 : « 3,50 par côté dans la devise du compte » → barème par devise (Annexe K) ; 3,50 = compte USD / exemple AUD PDS | Const. Annexe C p.59 (scellé : n/a) |
| 6 | Annexe A : règle d'envoi complète du §8.5 recopiée (rollover 23:55→00:10, séance ouverte/non suspendu, marge ≥ ρ_min, conv. unique) | Const. p.54 ; Scellé p.48 |
| 7 | Pseudo-code Annexe F : `units=1.0` → notionnel de référence 100 000 USD (`1e5/usd_price(start)`), DDD et Coût_total en USD | Const. p.65 ; Scellé |
| 8 | Tableau 8.10 « Bruit pur N(0;8) » : couple +3,06/0,7 % marqué v1.1 non reproductible ; recalcul inséré : ≈ +2,42, ≈ 0,31 % sous N(0;8) | Const. ; Scellé (son T8.10 avait déjà des valeurs différentes + avertissement simulation) |
| 9 | « 17:00 New York = 00:00 serveur toute l'année » → « en règle générale (dérive ±1 h, DST US/EU) » ; horodatages convertis en UTC | Const. p.36 §11.1 ; Scellé |
| 10 | « Le DDD initial est réalisé » → « le DDD initial est la marque à l'exécution — ce n'est pas du cash » (aligné §7/glossaire) | Const. p.10 ; Scellé |

## Incidents et réparations (round 2)

- **7 zones blanches** après `insert_textbox` retour `rc<0` (texte débordant la boîte) → réinsérées avec texte raccourci / taille réduite, contrôle `rc≥0` à chaque insertion.
- **Glyphes ⇔/∧ en carrés (□)** : Arial ne couvre pas ces symboles → réinsertion en **Segoe UI Symbol** (`fontname='FSYM'` distinct de `F0`=Arial, collision de nom de ressource initiale).
- **Résidus de ~2 px** (mots « lover », « S(h ») dépassant la boîte de redaction à x485 → redaction élargie à x545, badge Origine (« v1.4 » / « — ») réinséré.
- `os.replace` bloqué par OneDrive (WinError 5) à chaque étape → bascule manuelle des `.corr/.fix/.fix2.pdf`.

## Vérification finale

- Ré-extraction normalisée (NFC, NBSP→espace, U+037E→;) : **40/40 insertions présentes**, anciens libellés disparus.
- Rendu visuel des pages modifiées : lisible, contenu dans les cellules, aucun recouvrement.
- « 365 UK/AU » restants = drapeaux « écart À VÉRIFIER » + Annexe I fidèle à S3 + exemple recalculé Annexe K : intentionnels.

---

# Addendum R2 — Protocole d'exécution séquentielle non-atomique

Ajout de contenu normatif (script `add_addendum_r2.py`), décidé après vérification « open graph » : le document définissait les chemins ouverts mais ne modélisait pas l'exécution fill par fill. Contenu arrêté avec le commanditaire : ordres IOC tués immédiatement, fill partiel → redimensionnement si viable sinon débouclage (jamais de poursuite), débouclage au marché des jambes remplies en ordre inverse.

| Élément | Détail |
|---|---|
| Nouvelle page | Const. p.80 ; Scellé p.70 — titre « ADDENDUM R2 — EXÉCUTION SÉQUENTIELLE NON-ATOMIQUE DES CHEMINS OUVERTS », règles E1–E9 |
| Critère 2 §1 | « l'exécution est quasi simultanée, la position résiduelle étant un sous-produit et non un pari additionnel » → « l'exécution est **séquentielle et immédiate (Add. R2)** ; le résiduel est un sous-produit, non un pari » (Const. p.10 ; Scellé p.5) |
| Renvoi pseudo-code | « — voir Addendum R2. » / « — Addendum R2 » ajouté après « écrite dans l'ordre où elle s'exécute » (Const. p.65 ; Scellé p.54) |
| Règles posées | E1 ordres IOC à prix protégé, jamais d'ordre posé ; E2 durée de vie prédite ≥ T_seq×m_seq ; E3 recheck fraîcheur/spread/DDD avant chaque fill ; E4 continuation si E[net] ≥ −(C_unwind + m_abort) ; E5 partiel → resize si viable sinon unwind ; E6 unwind to flat en ordre inverse des fills ; E7 résiduel non prévu ≠ résiduel prévu ; E8 journal par fill ; E9 paramètres calibrés par Optuna (τ_fresh, τ_slip, m_seq, q_min, m_abort, N_u), objectif = P&L réalisable |
| Incidents | Premiers `insert_textbox` rc<0 (critère 2 trop long ; renvoi const trop large) → textes raccourcis, rc≥0 vérifié. Zones blanches réparées sur les `.add.pdf` avant bascule |
| Vérification | Extraction : tous marqueurs présents, ancien libellé disparu ; rendus p.5/10/54/65/70/80 inspectés — propres, aucun recouvrement |
| Sauvegardes | `*.pre_r3.pdf` |

## Limite connue (addendum)

La numérotation « Page N / N » des pages d'addendum les exclut implicitement du corpus scellé d'origine (79/69 pages) : volontaire — ce sont des pages d'ajout clairement marquées.

---

## Round 4 — audit profond : pseudo-code ddd_exec (notation)

**Erreur confirmée** (audit demandé par l'utilisateur) : `ddd_exec` retournait
`p.units * usd_price(p.last) − usd_price(p.first)` alors que `units₀ = 1e5/usd_price(start)`.
Le terme soustrait devait être le notionnel de départ en USD (Q₀·P₀ = 1e5), pas le prix
unitaire — dimensionnellement et numériquement faux pour tout départ non-USD (JPY : 0,0067
au lieu de 100 000). La formule normative §6.3/§7 (Score = P_arr·Πr − P_dep) était déjà
correcte ; seule la compression du pseudo-code était fautive.

| Document | Page | Avant | Après |
|---|---|---|---|
| Constitution | 65 | `− usd_price(p.first())` | `− 1e5 /* P_départ */` |
| Document scellé | 54 | `− usd_price(p.first)` | `− 1e5 /* P_départ */` |

Méthode : redaction du token + insert Courier 7,4 (mono, cohérent avec DejaVu-Sans-Mono du code).
Vérifié : extraction + rendu visuel (audit/r4_64.png, audit/r4_53.png). Backups : `backup_pdf/*.pre_r4.pdf`.

## Round 4 — corrections mc_sim.py (audit continu)

| Bug | Avant | Après |
|---|---|---|
| Plancher latence obsolète | `max(0.005, N(0.0025,…))` → latence réelle ≈ 5 ms | Plancher 1 ms (RTT) |
| Diffusion figée 10 ms/fill | monde évoluait ~4× trop par fill | sous-pas sommant exactement `lat` |
| Commission flat 3,50 | convention canonique seule | `COMMS[paire] = 3,50/P_base` (§8.7 production) + fix index arête→paire |

## Round 4b — décision utilisateur : seuil τ_persist sur le produit complet

**Décision arrêtée** : τ_persist s'applique au produit complet `p_persist` (et non à S(h) seul ;
forme ICM : S(h) × 1{Ĥ > 0,55}). Cohérent avec le pseudo-code (`p_persist() > P_MIN`) et avec
l'exemple G.3 qui appliquait déjà le seuil au produit.

| Document | Page | Avant | Après |
|---|---|---|---|
| Constitution | 34 (marge) | « seuil τ_persist : échelle unique — S(h) seul ou produit recalibré après BIR ; 0,40 valait pour S(h) seul, À CALIBRER » | « seuil τ_persist : produit complet p_persist (ICM : sans BIR) — tranché v2.2 ; valeur À CALIBRER ; [0 ; 1] » |
| Document scellé | 28 | « seuil τ_persist, échelle unique §10.4 ;   [0 ; 1] » | « τ_persist : produit complet ; ∈ [0 ; 1] » + glyphe ∈ restauré (Segoe Symbol, □ → ∈) |

Méthode : redaction + insert Arial 6.0/7.6 ; ∈ en Segoe UI Symbol (FSYM). Vérifié extraction + rendu (r4b_33.png, r4c_27.png). Backups `backup_pdf/*.pre_r4.pdf`.

---

# RETRAIT R5 — machinerie cycles fermés supprimée des documents canoniques

Décision utilisateur : les cycles fermés (toute longueur) présentent une complexité
faible → concurrence saturée → exclus définitivement du périmètre opérationnel.
Les chemins ouverts (mixés CFD plus tard) restent la seule opportunité réelle.

Retiré des DEUX PDFs canoniques (`audit/purge_cycles.py`, backups `.pre_purge.pdf`) :
- §6.4 complet (Bellman-Ford sur -log r(e), DetectCycles, SmartPath-FX référence)
- Règle opérationnelle #25 « cycles fermés » (règle #26 métal-croisé conservée)
- Lignes inventaire/historique/annexes citant Bellman-Ford ou DetectCycles
- Note §5.3 « 56 cycles nets de frais n'existerait pas »
- Intitulé « Arbitrage fermé vs ouvert » -> « Comparaison des approches »

Conservé (bornage, non opérationnel) :
- §1 comparatif chemins ouverts vs cycles fermés + Tableau 1.1
- TOC, surveillance métal-croisé (QC cotation, jamais chemins, jamais pont)
- Note CME « triangle logique » -> « croisement logique » (budget 4 fills)

Note R5 insérée sous Tableau 1.1 dans les deux docs : motif de l'exclusion.
Vérification post-purge : 0 occurrence opérationnelle de Bellman-Ford /
DetectCycles / triangle / arbitrage fermé hors note de retrait.

---

# BUG MOTEUR R6 — directions de chemins fantomes (poc_engine)

Detecte par le protocole de verification sur cand.npz : 702 701 candidats net>0
(96 % des evals) et max net = 23 293 396 USD — economiquement impossible.

Cause racine : `enum_paths` ne conservait pas le noeud de depart du DFS.
`_vectorize` et `exec_seq` supposaient que chaque chemin part de la BASE de sa
1ere arete — faux pour les 5 196 chemins (50,2 %) dont le DFS part du cote quote.
Consequence : jambes partant d'un noeud non incident a l'arete -> d=-1 silencieux
-> transitions fantomes (ex. JPY->USD "via" USDCHF a 1/ask) -> produits absurdement
grands. Ex. pi 3425 (vrai chemin JPY->USD->CHF->EUR) evalue comme
bid_USDJPY x 1/ask_USDCHF x 1/ask_EURCHF x up[EUR] ~ 23,4 M$.

Fix : `enum_paths` retourne (paths, starts) ; `P_S`/marche de `_vectorize` et
`start` d'`exec_seq` utilisent le vrai noeud de depart. Verifie : 0 transition
fantome sur les 10 360 chemins ; pi 3425 = JPY->USD->CHF->EUR.

Artefacts invalides conserves : cand.npz.buggy_r1, optuna.db.buggy_r1.
Rescan sur moteur corrige lance (14 paires, 3,83 M ticks).


## R7 — Fuite d'état inter-trials + quote future (audit POC, 2026-10-28)

**Symptôme** : trials Optuna ≥1 affichaient ~262 $/trade constant → objectifs 0,4–28,5 M$
incompatibles avec le scan (max 119,76 $). T0 baseline (eng frais) honnête : 436 k$.

**Chaîne causale (4 maillons)** :
1. objective_factory partage un seul Engine entre tous les trials.
2. _advance (self.pos) ne pouvait qu'avancer — jamais remonter. Au replay du
   jour 22 après le jour 23 (trial ≥1), les curseurs restaient en fin de données →
   état « figé » fin jour 26 exposé pour tout le jour 22.
3. tick/sync posaient la quote même quand ts[j] > t (quote future).
4. Fraîcheur unilatérale (t - last_t) <= fresh_ms : last_t futur → passe.
Résultat : évaluations fantômes ; exec_seq remplissait les jambes aux vraies
quotes (quote_at stateless) mais marquait les résidus via usd_price figé →
biais de marquage systématique ~+0,26 % → 262 $/trade.

**Reproduction** : mêmes params T1 sur eng frais → 58 484 $ (j22+j23) vs 10 520 804 $
en DB. Intermediates T1 : j22 live = 6 680 852 $ vs 57 750 $ rejoué.

**Correctifs** (poc_engine.py) :
- _advance : rembobinage par searchsorted quand pos est en avance sur t.
- tick/sync : quote exposée seulement si j >= 0 and ts[j] <= t, sinon NaN.
- eval_at : fraîcheur bilatérale (last_t <= t) & (t - last_t <= fresh_ms).

**Tests** (test_r7.py) : T1 pas de quote future (AUDCHF 1er tick +4h) · T2 chemin
sans donnée non sélectionnable · T3 quote futur+500ms rejetée · T4 rembobinage
j23→j22 re-expose les vrais ticks · T5 eval nominale · T6 rejouabilité
j22|j23|j22 sur même eng → identique.

**Artefacts empoisonnés** : optuna.db (trials T1+) → archivé buggy_r2, non supprimé.

---

## Audit logique complet — 2e passe (nov. 2026, post-OG2)

Relecture ligne à ligne de tout le moteur (engine/eval/exec/scan/data/pybind)
et parité POC. Verdict : propre, sauf une classe — validation d'entrée.

### A — ingest acceptait les quotes croisées/NaN (corrigé)

`Engine::ingest` publiait et poussait dans la médiane toute quote, y
compris `ask < bid` ou NaN. Une quote croisée produit un spread négatif →
passe `fx_ok` (spread < cap x médian) → edge fantôme exécutable en eval.
Le client filtre en amont mais le moteur n'avait pas de défense en
profondeur. Garde ajoutée : `!finite || ask < bid` -> rejet silencieux
(ni état ni médiane). Test : `ingest_rejects_crossed_or_nonfinite_quotes`.

### B — data.rs : littéraux NaN + croisées non comptés rejetés (corrigé)

`"NaN".parse::<f64>()` réussit en Rust : le correctif OG2-B3 ne comptait
donc ni les NaN littéraux ni les quotes croisées des CSV comme lignes
rejetées — elles étaient chargées comme ticks valides. Validation
post-parse ajoutée : non fini ou `ask < bid` -> `rejected += 1`, mêmes
seuils 5 % que B3. Test : `nan_and_crossed_lines_are_rejected`.

### Doc

- `ABORT_COST_PER_LEG_USD` : commentaire clarifié (repli médiane absente,
  pas le modèle — le coût réel est par arête depuis OG2-D2).
- pybind `exec_settle` : commentaire `-12*filled` obsolète remplacé par
  `abort_cost(legs, filled)`.

### Vérifié propre (sans changement)

- eval.rs : direction bid/1-ask, comm `COMM/up[base]` = modèle
  per-100k-base-ccy (~2.31-5.00 $/jambe), argmax strict = np.argmax,
  Convention A `up_liq`, NaN rattrapés par le check final.
- exec.rs : ordre des portes E1-E9 identique au POC (q_send à t, q_now à
  t_f, kill IOC >3t, frac {1, 0.6}, vol_guard côté atteint, liquidation
  partielle exécutable, `t = t_f` séquentiel).
- scan.rs : re-détection sous params du trial, verrou `t_free`,
  cooldown s->ms correct, épisode non fermé sur eval None (parité POC).
- poc_engine.py : parité ligne à ligne confirmée (eval vectorisée,
  _advance/tick/sync, usd_price/_usd_liq, exec_seq, _abort par arête).
- ticks_to_poc.py : EPOCH UTC (corrigé OG2-B4).

Tests : 35/35 engine + 60/60 client verts ; release rebuild.
