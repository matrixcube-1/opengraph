# -*- coding: utf-8 -*-
"""Répare les zones où insert_textbox a échoué (rc<0 -> redaction sans texte).
Insère directement dans les PDFs déjà corrigés ; vérifie rc >= 0 par insertion.
"""
import os, pymupdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARIAL = r"C:\Windows\Fonts\arial.ttf"
R = pymupdf.Rect

# (fichier, page, rect d'insertion, texte, fontsize de départ)
FIXES = [
    ("Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 3,
     R(425, 517.5, 502, 529.5), "360 sinon (écart, §8.8)", 6.0),
    ("Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 32,
     R(65, 245.5, 500, 259), "Ajust_div_USD = ΔI × valeur_du_point ; "
     "coût : −ΔI×VP long (crédit), +ΔI×VP court (débit) ; "
     "ΔI = I × w × D / P_action (points)", 5.2),
    ("Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 55,
     R(303, 364, 485, 378), "long crédité, court débité ; USD : ΔI×VP (§8.8)",
     6.0),
    ("Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 67,
     R(279, 596, 497, 611), "un seuil de 0,40 appliqué au produit que si "
     "BIR_aj ≥ 0,51 — sans effet ici,", 4.8),
    ("Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 25,
     R(65, 732.5, 479, 745), "Ajust_div_USD = ΔI × valeur_du_point ; "
     "coût : −ΔI×VP long (crédit), +ΔI×VP court (débit) ; "
     "ΔI = I × w × D / P_action (points)", 5.2),
    ("Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 49,
     R(301, 341, 485, 355), "long crédité, court débité ; USD : ΔI×VP (§8.8)",
     6.0),
    ("Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 56,
     R(279, 596, 497, 611), "un seuil de 0,40 appliqué au produit que si "
     "BIR_aj ≥ 0,51 — sans effet ici,", 4.8),
]


def try_insert(page, rect, text, fs):
    """Retourne le rc de insert_textbox (en cas d'échec ne laisse rien)."""
    return page.insert_textbox(rect, text, fontname="F0", fontfile=ARIAL,
                               fontsize=fs, color=(0, 0, 0))


def main():
    docs = {}
    for f, pno, rect, text, fs0 in FIXES:
        if f not in docs:
            docs[f] = pymupdf.open(os.path.join(BASE, f))
        doc = docs[f]
        page = doc[pno - 1]
        fs = fs0
        while True:
            rc = try_insert(page, rect, text, fs)
            if rc >= 0:
                print(f"{f[:20]} p{pno}: OK fs={fs:.1f} rc={rc:.1f}")
                break
            if fs <= 3.0:
                print(f"{f[:20]} p{pno}: ECHEC fs={fs:.1f} rc={rc:.1f} "
                      f"-> {text[:40]!r}")
                break
            fs -= 0.4
    for f, doc in docs.items():
        out = os.path.join(BASE, f.replace(".pdf", ".fix.pdf"))
        doc.save(out, garbage=3, deflate=True)
        os.replace(out, os.path.join(BASE, f))
        doc.close()
        print(f"{f}: sauvegardé")


if __name__ == "__main__":
    main()
