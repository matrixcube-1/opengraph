# -*- coding: utf-8 -*-
"""Localisation des cibles de correction round 2 dans les deux PDFs."""
import os, pymupdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = {
    "CONST": "Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf",
    "SCELLE": "Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf",
}

TARGETS = [
    ("dividende", ["long", "débité,", "court", "crédité"]),
    ("ajust_div", ["Ajust_div", "(indice", "cash,", "long)", "=", "−I"]),
    ("cout_ajustdiv", ["Ajust_div", "−", "Ajust_roll"]),
    ("seuil_040", ["seuil", "0,40"]),
    ("seuil_040a", ["(seuil", "0,40"]),
    ("seuil_040b", ["indicatif", "0,40"]),
    ("d365_uka", ["365", "(UK,", "AU)"]),
    ("d365_ukb", ["365", "UK/AU"]),
    ("c28", ["Concordants", ":", "3,50", "par", "côté"]),
    ("regle_annexA", ["ENVOYER", "⇔", "DDD_eff", ">", "Coût_total", "∧", "spread"]),
    ("units_10", ["/*units=*/1.0,"]),
    ("t810", ["+3,06,", "0,7", "%", "des", "lectures"]),
    ("horloge", ["17:00", "New", "York", "=", "00:00", "serveur"]),
    ("ddd_realise", ["réalisé", "à", "l'instant", "d'exécution"]),
    ("persist_annexA", ["p_persist", "=", "S(h)"]),
]


def find_seq(ws, seq):
    hits, cur = [], []
    for w in ws:
        if w[4] == seq[len(cur)]:
            cur.append(w)
            if len(cur) == len(seq):
                hits.append(list(cur)); cur = []
        else:
            cur = [w] if w[4] == seq[0] else []
    return hits


for tag, path in DOCS.items():
    doc = pymupdf.open(os.path.join(BASE, path))
    print(f"===== {tag} ({doc.page_count} p.) =====")
    for name, seq in TARGETS:
        found = []
        for pno in range(doc.page_count):
            for h in find_seq(doc[pno].get_text("words"), seq):
                r = pymupdf.Rect(h[0][:4])
                for w in h[1:]:
                    r |= pymupdf.Rect(w[:4])
                found.append((pno + 1, tuple(round(v, 1) for v in r)))
        print(f"  {name:<15} {found if found else '—'}")
    doc.close()
