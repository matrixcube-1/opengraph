# -*- coding: utf-8 -*-
"""Dump mot-par-mot autour des sites cibles, pour calibrer les edits."""
import pymupdf, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def around(pdf, page_no, needle, ctx=14):
    doc = pymupdf.open(os.path.join(BASE, pdf))
    ws = doc[page_no - 1].get_text("words")
    idxs = [i for i, w in enumerate(ws) if needle in w[4]]
    print(f"--- {pdf[:14]} p{page_no} ~ {needle!r} ({len(idxs)} hits) ---")
    for i in idxs[:4]:
        lo = max(0, i - ctx); hi = min(len(ws), i + ctx)
        for w in ws[lo:hi]:
            print(f"   {w[5]}.{w[6]} ({w[0]:.0f},{w[1]:.0f}) {w[4]!r}")
        print("   ...")
    doc.close()

jobs = [
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 12, "FX(de"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 31, "FX"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 27, "Impact"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 29, "(spread"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 29, "*"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 55, "P_S"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 36, "0,30"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 44, "Dix"),
 (r"Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf", 22, "v1.5"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 6, "posait"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 7, "applique"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 7, "Statut"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 23, "règle"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 23, "La,"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 24, "F05"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 24, "simulé"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 24, "8,09"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 24, "§8"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 25, "FX"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 49, "P_S"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 49, "—"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 38, "Dix"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 69, "Fin"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 21, "Impact"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 23, "(spread"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 54, "net"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 54, "collés"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 4, "net"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 18, "net"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 32, "C15"),
 (r"Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf", 44, "C15"),
]
for j in jobs:
    try:
        around(*j)
    except Exception as e:
        print(j, "ERR", e)
