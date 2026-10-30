#!/usr/bin/env python3
"""Purge machinerie cycles fermés / Bellman-Ford — décision utilisateur (round 5).

RETIRÉ : §6.4 complet, clause BF de la règle 25, lignes inventaire/historique BF,
         DetectCycles, cellules SmartPath-BF, mentions résumé/annexes.
GARDÉ  : §1 comparatif (bornage), note CME « fills » (remotée « triangle » ->
         « croisement »), surveillance métal-croisé (QC courtier), moteur graphe
         SmartPath-FX hors cycles.

Chaque retrait est explicite ci-dessous — auditable et rejouable.
Usage : python purge_cycles.py [--dry]
"""
import shutil
import sys
from pathlib import Path

import pymupdf

ROOT = Path(r"C:\Users\EVROSIS\OneDrive\Desktop\Arbitrage_FX")
BAK = ROOT / "backup_pdf"
CONS = ROOT / "Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf"
SCEL = ROOT / "Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf"

# (page 1-based, phrase exacte) — rédaction ponctuelle
REMOVE = {
    CONS: [
        (1,  "Bellman-Ford (cycles fermés, référence) ; "),
        (4,  ", Bellman-Ford hors chemin critique"),
        (6,  ", Bellman-Ford"),
        (23, "Bellman-Ford"),
        (25, "(4) Garder Bellman-Ford hors chemin critique. "),
        (40, "à la manière de Bellman-Ford pour les cycles FX."),
        (45, "Bellman-Ford reste hors du chemin critique (référence cycles fermés) ; "),
        (48, "Bellman-Ford / "),
        (49, "Bellman-Ford cycles fermés"),
        (49, "−log r, détection de cycle négatif ; correctement classé « référence non"),
        (49, "déployée »."),
        (54, "Bellman-Ford (référence)"),
        (54, "w(e) = −log r(e) ; cycle Σ w < 0 ⇒ arbitrage fermé"),
        (56, " (Bellman-Ford pour cycles)"),
        (68, ", Bellman-Ford"),
        (24, "Bellman-Ford −log comme module de référence cycles fermés"),
        (24, "Frais plats ignorés dans DetectCycles"),
        (8,  " vs Bellman-Ford"),
        (10, "Bellman-Ford "),
    ],
    SCEL: [
        (1,  "Bellman-Ford: note secondaire uniquement (cycles de référence, non déployé). "),
        (39, "Bellman-Ford reste hors du chemin critique (référence cycles fermés); "),
        (19, "Garder Bellman-Ford hors chemin critique. "),
        (19, "Bellman-Ford −log comme module de référence cycles fermés"),
        (19, "Frais plats ignorés dans DetectCycles"),
        (42, "Bellman-Ford / "),
        (43, "Bellman-Ford cycles fermés"),
        (43, "−log r, détection de cycle négatif; correctement classé « référence non"),
        (43, "déployée »."),
        (48, "Bellman-Ford (référence)"),
        (48, "w(e) = −log r(e); cycle Σ w < 0 ⇒ arbitrage fermé"),
        (57, ", Bellman-Ford"),
        (5,  "Bellman-Ford "),
    ],
}

# bloc §6.4 complet : (pdf, page, marqueur_début, marqueur_fin)
BLOCKS = [
    (CONS, 24, "6.4 Bellman-Ford",
     "frais plats sont ignorés dans DetectCycles (TODO amont)."),
    (SCEL, 19, "6.4 Note secondaire",
     "frais plats sont ignorés dans DetectCycles (TODO amont)."),
]

# « triangle » -> « croisement » (note budget fills CME conservée, mot retiré)
REWORD = [
    (CONS, 23, "« triangle »", "« croisement »"),
    (CONS, 41, "« triangle »", "« croisement »"),
    (SCEL, 18, "« triangle »", "« croisement »"),
    (SCEL, 35, "« triangle »", "« croisement »"),
]


def backup(pdf):
    dst = BAK / (pdf.stem + ".pre_purge.pdf")
    if not dst.exists():
        shutil.copy2(pdf, dst)
        print("backup:", dst.name)


def main():
    dry = "--dry" in sys.argv
    for pdf, removes in REMOVE.items():
        backup(pdf)
        doc = pymupdf.open(pdf)
        tot = 0
        # 1) rédactions ponctuelles
        for page1, ph in removes:
            hits = doc[page1 - 1].search_for(ph)
            if not hits:
                print(f"  ?? {pdf.name} p{page1} absent: {ph[:55]}")
            for r in hits:
                doc[page1 - 1].add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5),
                                                fill=(1, 1, 1))
            tot += len(hits)
        # 2) blocs §6.4
        for pdf_b, page1, s0, s1 in BLOCKS:
            if pdf_b is not pdf:
                continue
            p = doc[page1 - 1]
            r0, r1 = p.search_for(s0), p.search_for(s1)
            if not r0 or not r1:
                print(f"  !! bloc absent {pdf.name} p{page1}")
                continue
            rect = pymupdf.Rect(40, min(r.y0 for r in r0) - 2,
                                p.rect.width - 40, max(r.y1 for r in r1) + 2)
            p.add_redact_annot(rect, fill=(1, 1, 1)); tot += 1
        # 3) remplacement « triangle » -> « croisement »
        for pdf_r, page1, old, new in REWORD:
            if pdf_r is not pdf:
                continue
            hits = doc[page1 - 1].search_for(old)
            for r in hits:
                doc[page1 - 1].add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5),
                                                fill=(1, 1, 1), text=new,
                                                fontname="helv", fontsize=9.5)
            tot += len(hits)
        print(f"{pdf.name}: {tot} zones ciblées")
        if not dry:
            for p in doc:
                p.apply_redactions()
            out = pdf.with_suffix(".purged.pdf")
            doc.save(out, garbage=3, deflate=True)
            print("écrit:", out.name)
        doc.close()


if __name__ == "__main__":
    main()
