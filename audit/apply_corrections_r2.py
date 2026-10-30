# -*- coding: utf-8 -*-
"""Corrections documentaires round 2 — Constitution v2.2 + Document scellé (27/10/2026).

10 écarts vérifiés dans le texte / contre la PDS (table utilisateur).
Même méthode que apply_corrections.py : localisation mot -> redaction -> réinsertion Arial.
Usage : python apply_corrections_r2.py [dry|apply]
"""
import sys, os, pymupdf
from apply_corrections import run

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONST = "Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf"
SCELLE = "Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf"

R = pymupdf.Rect

REGLE_FULL = ("ENVOYER ⇔ DDD_eff > Coût_total (conv. unique A ou B) ∧ spread ≤ "
              "1,7 × normal/jambe ∧ hors annonce ∧ hors rollover 23:55→00:10 "
              "serveur ∧ séance ouverte, non suspendu ∧ S(h + lat) > 0,30 ∧ "
              "DDD_eff,CFD > 0 ∧ marge ≥ ρ_min ∧ intra-venue (= §8.5)")
AJUSTDIV = ("Ajust_div_USD = ΔI × valeur_du_point ; en coût : −ΔI×VP (long, "
            "crédité) / +ΔI×VP (court, débité) ; ΔI = I × w × D / P_action "
            "(points) — une seule fois (coût ou DDD)")
SEUIL_104 = ("(seuil τ_persist : échelle unique — S(h) seul ou produit "
             "recalibré après BIR ; 0,40 valait pour S(h) seul, À CALIBRER ; "
             "∈ [0 ; 1])")
DIV_A = "long crédité, court débité ; en USD : ΔI × valeur du point (§8.8)"
T810 = "≈ +2,42 ; ≈ 0,31 % sous N(0 ; 8) — simulé v1.1 non reproduit"
HORLOGE = ("soit 17:00 New York = 00:00 serveur en règle générale (dérive "
           "±1 h, DST US/EU).")
DDD_MARQUE = ("Le DDD initial est la marque à l'exécution — ce n'est pas du "
              "cash (§7) ; c'est la prime d'illiquidité captée.")
UNITS_C = ("/*units=*/1e5/usd_price(start), visited={start}, out); // état "
           "(nœud,k) ; notionnel 100 000 USD — DDD et coûts en USD")
G3 = "un seuil de 0,40 appliqué au produit que si BIR_aj ≥ 0,51 — sans effet ici,"
C28_NEW = ("3,50/côté = compte USD (et exemple AUD de la PDS) ; sinon barème "
           "par devise du compte (Annexe K) : EUR 2,75 ; GBP 2,50 ; CHF 3,30 ; "
           "JPY 325.")

FOOT_C = ("Financement : 3 % ± taux de référence (S3 : « Libor ») ; f = (S × P "
          "× r) / d ; d = 360 (PDS §11.2 ; « 365 UK/AU » S3 : écart, "
          "À VÉRIFIER) ; appliqué à/après 17:00 heure de New York ; aucun sur "
          "les futures CFD.")

D360_TAIL = ("d = 360 (PDS §11.2 ; « 365 UK/AU » S3 : écart, À VÉRIFIER) ; "
             "à/après 17:00 New York")
D360_A = ("d = 360 (PDS §11.2 ; « 365 UK/AU » S3 : écart, À VÉRIFIER) ; "
          "×3 le vendredi")

C = [
    # ---- 1+2. Dividende : signe PDS + passage en USD ----------------------
    dict(kind="rep", page=55, seq=["long", "débité,", "court", "crédité"],
         new=DIV_A, ins=R(303, 364, 485, 378), tag="1 dividende Annexe A"),
    dict(kind="rep", page=32,
         seq=["Ajust_div", "(indice", "cash,", "long)", "=", "−I", "×", "w",
              "×", "D", "/", "P_action", "(points", "d'indice", ";", "court",
              ":", "+)"],
         new=AJUSTDIV, xr=0, tag="2 Ajust_div USD §8.8"),
    dict(kind="rep", page=29, seq=["Ajust_div", "−", "Ajust_roll"],
         new="Ajust_div_USD − Ajust_roll", xr=5, tag="2 Coût_total USD"),
    # ---- 3. p_persist : échelle de seuil unique ---------------------------
    dict(kind="rep", page=34,
         seq=["(seuil", "0,40", ";", "∈", "[0", ";", "1])"],
         new=SEUIL_104, ins=R(372, 653, 545, 676), tag="3 seuil §10.4"),
    dict(kind="rep", page=30, seq=["seuil", "indicatif", "0,40)"],
         new="seuil τ_persist, §10.4)", xr=0, tag="3 seuil §8.5"),
    dict(kind="rep", page=44, seq=["(indicatif", "0,40,", "à", "calibrer)"],
         new="(τ_persist, §10.4)", ins=R(84, 698, 178, 712),
         tag="3 seuil règle 22"),
    dict(kind="rep", page=46, seq=["0,40", "(indicatif)"],
         new="τ_persist (§10.4)", ins=R(376, 346, 460, 360), tag="3 seuil §17"),
    dict(kind="rep", page=54, seq=["0,40)", ";", "IC", "Markets", "sans"],
         new="τ_persist) ; IC Markets sans", xr=15, tag="3 seuil Annexe A"),
    dict(kind="rep", page=67,
         seq=["le", "seuil", "indicatif", "0,40", "que", "si", "BIR_aj", "≥",
              "0,51", "—", "sans", "effet", "ici,"],
         new=G3, ins=R(279, 596, 498, 611), tag="3 seuil G.3"),
    # ---- 4. d = 365 UK/AU -> 360 ------------------------------------------
    dict(kind="rep", page=3, seq=["365", "UK/AU,", "360", "sinon"],
         new="360 sinon (écart → §8.8)", ins=R(425, 518, 502, 529),
         tag="4 d I-6"),
    dict(kind="rep", page=17,
         seq=["Financement", ":", "3", "%", "±", "taux", "de", "référence",
              "(S3", ":", "«", "Libor", "»)", ";", "f", "=", "(S", "×", "P",
              "×", "r)", "/", "d", ";", "d", "=", "365", "(UK,", "AU),", "360",
              "sinon", ";", "appliqué", "à/après", "17:00", "heure", "de",
              "New", "York", ";", "aucun", "sur", "les", "futures", "CFD."],
         new=FOOT_C, ins=R(211, 412, 545, 438), tag="4 d Tableau 3.7"),
    dict(kind="rep", page=31,
         seq=["d", "=", "365", "UK/AU,", "360", "sinon", ";", "à/après",
              "17:00", "New", "York"],
         new=D360_TAIL, ins=R(150, 706, 360, 720), tag="4 d Tableau 8.13"),
    dict(kind="rep", page=55,
         seq=["d", "=", "365", "(UK,", "AU)", "ou", "360", ";", "×3", "le",
              "vendredi"],
         new=D360_A, ins=R(287, 268, 485, 283), tag="4 d Annexe A"),
    dict(kind="rep", page=70,
         seq=["autres", ";", "à/après", "17:00", "New", "York", ";", "pas",
              "de", "financement", "sur", "futures", "CFD"],
         new="autres — écart vs exemple S4 §11.2 : /360 (À VÉRIFIER, §8.8) ; "
             "à/après 17:00 New York ; pas de financement sur futures CFD",
         ins=R(165, 741, 425, 756), tag="4 d Annexe I"),
    # ---- 5. C28 -----------------------------------------------------------
    dict(kind="rep", page=59,
         seq=["Concordants", ":", "3,50", "par", "côté", "dans", "la",
              "devise", "du", "compte", "(AUD", "pour", "la", "PDS",
              "australienne)."],
         new=C28_NEW, ins=R(400, 633, 545, 670), tag="5 C28 concordance"),
    # ---- 6. Annexe A : règle d'envoi complète ------------------------------
    dict(kind="rep", page=54,
         seq=["ENVOYER", "⇔", "DDD_eff", ">", "Coût_total", "∧", "spread",
              "≤", "1,7", "×", "normal", "∧", "hors", "annonce", "∧", "S(h",
              "+", "lat)", ">", "0,30", "∧", "DDD_eff,CFD", ">", "0", "∧",
              "intra-venue"],
         new=REGLE_FULL, ins=R(176, 682, 485, 716), tag="6 règle Annexe A"),
    # ---- 7. Annexe F : units -> notionnel ----------------------------------
    dict(kind="rep", page=65,
         seq=["/*units=*/1.0,", "visited={start},", "out);", "//", "état",
              "(nœud,", "k)"],
         new=UNITS_C, ins=R(196, 480, 545, 492), tag="7 units notionnel"),
    # ---- 8. Tableau 8.10 ---------------------------------------------------
    dict(kind="rep", page=30, seq=["+3,06,", "0,7", "%", "des", "lectures"],
         new=T810, ins=R(441, 308, 545, 324), tag="8 T8.10 bruit pur"),
    # ---- 9. Horloge ---------------------------------------------------------
    dict(kind="rep", page=36,
         seq=["soit", "17:00", "New", "York", "=", "00:00", "serveur",
              "toute", "l'année."],
         new=HORLOGE, ins=R(363, 327, 545, 341), tag="9 horloge DST"),
    # ---- 10. DDD « réalisé » ------------------------------------------------
    dict(kind="rep", page=10,
         seq=["Le", "DDD", "initial", "est", "réalisé", "à", "l'instant",
              "d'exécution", ":", "c'est", "la", "prime", "d'illiquidité",
              "captée."],
         new=DDD_MARQUE, ins=R(54, 477, 417, 493), tag="10 DDD marque"),
]

S = [
    dict(kind="rep", page=49, seq=["long", "débité,", "court", "crédité"],
         new=DIV_A, ins=R(301, 341, 485, 355), tag="1 dividende Annexe A"),
    dict(kind="rep", page=25,
         seq=["Ajust_div", "(indice", "cash,", "long)", "=", "−I", "×", "w",
              "×", "D", "/", "P_action", "(points", "d'indice;", "court:",
              "+)"],
         new=AJUSTDIV, xr=0, tag="2 Ajust_div USD §8.8"),
    dict(kind="rep", page=23, seq=["Ajust_div", "−", "Ajust_roll"],
         new="Ajust_div_USD − Ajust_roll", ins=R(449, 126, 545, 140),
         tag="2 Coût_total USD"),
    dict(kind="rep", page=28,
         seq=["(seuil", "0,40;", "∈", "[0;", "1])", "Ĥ:", "estimateur", "R/S",
              "(§10.2)"],
         new="(seuil τ_persist, échelle unique §10.4 ; ∈ [0 ; 1])   "
             "Ĥ : estimateur R/S (§10.2)",
         ins=R(65, 653, 315, 665), tag="3 seuil §10.4"),
    dict(kind="rep", page=23, seq=["seuil", "indicatif", "0,40)"],
         new="seuil τ_persist, §10.4)", xr=0, tag="3 seuil §8.5"),
    dict(kind="rep", page=38, seq=["(indicatif", "0,40,", "à", "calibrer)"],
         new="(τ_persist, §10.4)", ins=R(84, 698, 178, 712),
         tag="3 seuil règle 22"),
    dict(kind="rep", page=40, seq=["0,40", "(indicatif)"],
         new="τ_persist (§10.4)", ins=R(376, 334, 460, 349), tag="3 seuil §17"),
    dict(kind="rep", page=48, seq=["0,40);", "IC", "Markets", "sans"],
         new="τ_persist); IC Markets sans", xr=15, tag="3 seuil Annexe A"),
    dict(kind="rep", page=56,
         seq=["le", "seuil", "indicatif", "0,40", "que", "si", "BIR_aj", "≥",
              "0,51", "—", "sans", "effet", "ici,"],
         new=G3, ins=R(279, 596, 498, 611), tag="3 seuil G.3"),
    dict(kind="rep", page=12,
         seq=["Financement:", "3", "%", "±", "taux", "de", "référence",
              "(S3:", "«", "Libor", "»);", "f", "=", "(S", "×", "P", "×",
              "r)", "/", "d;", "d", "=", "365", "(UK,", "AU),", "360",
              "sinon;", "appliqué", "à/après", "17:00", "heure", "de", "New",
              "York;", "aucun", "sur", "les", "futures", "CFD."],
         new=FOOT_C, ins=R(212, 383, 545, 408), tag="4 d Tableau 3.7"),
    dict(kind="rep", page=25,
         seq=["d", "=", "365", "UK/AU,", "360", "sinon;", "à/après", "17:00",
              "New", "York"],
         new=D360_TAIL, ins=R(150, 500, 360, 513), tag="4 d Tableau 8.13"),
    dict(kind="rep", page=49,
         seq=["d", "=", "365", "(UK,", "AU)", "ou", "360;", "×3", "le",
              "vendredi"],
         new=D360_A, ins=R(283, 257, 485, 271), tag="4 d Annexe A"),
    dict(kind="rep", page=59,
         seq=["autres;", "à/après", "17:00", "New", "York;", "pas", "de",
              "financement", "sur", "futures", "CFD"],
         new="autres — écart vs exemple S4 §11.2 : /360 (À VÉRIFIER, §8.8) ; "
             "à/après 17:00 New York ; pas de financement sur futures CFD",
         ins=R(165, 741, 425, 756), tag="4 d Annexe I"),
    dict(kind="rep", page=48,
         seq=["ENVOYER", "⇔", "DDD_eff", ">", "Coût_total", "∧", "spread",
              "≤", "1,7", "×", "normal", "∧", "hors", "annonce", "∧", "S(h",
              "+", "lat)", ">", "0,30", "∧", "DDD_eff,CFD", ">", "0", "∧",
              "intra-venue"],
         new=REGLE_FULL, ins=R(176, 682, 485, 716), tag="6 règle Annexe A"),
    dict(kind="rep", page=54, seq=["/*units=*/1.0,"],
         new="/*units=*/1e5/usd_price(start),", ins=R(454, 370, 545, 380),
         tag="7 units notionnel"),
    dict(kind="rep", page=54, seq=["//", "état", "(nœud,", "k)"],
         new="// état (nœud,k) ; notionnel = 100 000 USD (même unité que "
             "Coût_total)",
         ins=R(165, 380, 545, 392), tag="7 commentaire notionnel"),
    dict(kind="rep", page=24, seq=["+3,06,", "0,7", "%", "des", "lectures"],
         new=T810, ins=R(441, 167, 545, 183), tag="8 T8.10 bruit pur"),
    dict(kind="rep", page=30,
         seq=["soit", "17:00", "New", "York", "=", "00:00", "serveur",
              "toute", "l'année."],
         new=HORLOGE, ins=R(360, 314, 545, 329), tag="9 horloge DST"),
    dict(kind="rep", page=5,
         seq=["Le", "DDD", "initial", "est", "réalisé", "à", "l'instant",
              "d'exécution:", "c'est", "la", "prime", "d'illiquidité",
              "captée."],
         new=DDD_MARQUE, ins=R(54, 477, 417, 493), tag="10 DDD marque"),
]


def main():
    app = len(sys.argv) > 1 and sys.argv[1] == "apply"
    print("=== CONSTITUTION ===")
    rep = run(CONST, C, app)
    print("=== SCELLE ===")
    rep += run(SCELLE, S, app)
    print("--- résumé ---")
    miss = [r for r in rep if r[2] == "MISS"]
    for pno, tag, st, x in rep:
        print(f"  p{pno:>3} [{tag}] {st} {x[:60]}")
    print(f"{len(rep) - len(miss)} OK, {len(miss)} MISS")


if __name__ == "__main__":
    main()
