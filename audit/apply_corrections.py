# -*- coding: utf-8 -*-
"""Corrections chirurgicales des PDFs Arbitrage_FX (revue critique, 25/10/2026).

Localisation au niveau mot -> redaction (fond blanc) -> reinsertion en Arial.
Usage : python apply_corrections.py [dry|apply]
"""
import sys, os, pymupdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARIAL = r"C:\Windows\Fonts\arial.ttf"
CONST = "Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf"
SCELLE = "Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf"
RM = 545


def find_seq(ws, seq, occurrence=0):
    hits, cur = [], []
    for w in ws:
        if w[4] == seq[len(cur)]:
            cur.append(w)
            if len(cur) == len(seq):
                hits.append(list(cur)); cur = []
        else:
            cur = [w] if w[4] == seq[0] else []
    return hits[occurrence] if len(hits) > occurrence else None


def union(ws_list):
    return pymupdf.Rect(min(w[0] for w in ws_list) - 0.5,
                        min(w[1] for w in ws_list) - 0.5,
                        max(w[2] for w in ws_list) + 0.5,
                        max(w[3] for w in ws_list) + 0.5)


def by_line(ws_list):
    out = {}
    for w in ws_list:
        out.setdefault((w[5], w[6]), []).append(w)
    return list(out.values())


def fontsize_at(page, rect):
    sizes = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if pymupdf.Rect(s["bbox"]).intersects(rect):
                    sizes.append(s["size"])
    return max(set(sizes), key=sizes.count) if sizes else 7.0


def est_width(text, fs):
    return pymupdf.get_text_length(text, fontname="helv", fontsize=fs) * 1.08


def longest_word(text):
    return max((len(w) for w in text.split()), default=0)


def insert_fit(page, rect, text, fs0):
    fs = min(fs0, 8.0)
    while fs > 3.0:
        w = est_width(text, fs)
        lines = max(1, -(-w // max(1, rect.width)))
        ok_h = lines * fs * 1.35 <= rect.height
        ok_w = est_width("X" * longest_word(text), fs) <= rect.width
        if ok_h and ok_w:
            break
        fs -= 0.4
    rc = page.insert_textbox(rect, text, fontname="F0", fontfile=ARIAL,
                             fontsize=fs, color=(0, 0, 0))
    return fs, rc


def plan_page(page, edits, report, pno):
    ws = page.get_text("words")
    jobs = []
    for e in edits:
        hit = find_seq(ws, e["seq"], e.get("occ", 0))
        if not hit:
            report.append((pno, e.get("tag"), "MISS", str(e["seq"][:3])))
            continue
        rects = [union(g) for g in by_line(hit)]
        if e["kind"] == "delpart":
            rects = [union([hit[i]]) for i in e["idx"]]
        fs0 = fontsize_at(page, rects[0])
        for r in rects:
            page.add_redact_annot(r, fill=(1, 1, 1), cross_out=False)
        ins = e.get("ins")
        if ins is None and "new" in e:
            ins = pymupdf.Rect(rects[0].x0, rects[0].y0 - 0.5,
                               min(RM, max(r.x1 for r in rects) + e.get("xr", 0)),
                               rects[-1].y1 + e.get("yd", 1.5))
        jobs.append((rects, e.get("new"), ins, fs0, e.get("tag", "?")))
    return jobs


def run(pdf, edits, apply_mode, doc=None, extra_cells=None):
    own = doc is None
    if own:
        doc = pymupdf.open(os.path.join(BASE, pdf))
    byp = {}
    for e in edits:
        byp.setdefault(e["page"], []).append(e)
    report = []
    for pno in sorted(byp):
        page = doc[pno - 1]
        jobs = plan_page(page, byp[pno], report, pno)
        if not apply_mode:
            for rects, new, ins, fs0, tag in jobs:
                print(f"  p{pno} [{tag}] fs~{fs0:.1f} "
                      f"{[tuple(round(v) for v in r) for r in rects]}"
                      + (f" ins={tuple(round(v) for v in ins)}" if ins else "")
                      + (f" -> {new!r}" if new else ""))
            continue
        page.apply_redactions()
        for rects, new, ins, fs0, tag in jobs:
            if new is None:
                report.append((pno, tag, "DEL-OK", ""))
            else:
                fs, rc = insert_fit(page, ins, new, fs0)
                report.append((pno, tag, f"INS fs={fs:.1f} rc={rc:.0f}", new[:50]))
    if extra_cells:
        for pno, cells in extra_cells.items():
            page = doc[pno - 1]
            if not apply_mode:
                for r, val in cells:
                    print(f"  p{pno} [Origine col] {tuple(round(v) for v in r)} -> {val!r}")
                continue
            for r, val in cells:
                page.add_redact_annot(pymupdf.Rect(r.x0, r.y0, r.x0 + 8, r.y1),
                                      fill=(1, 1, 1), cross_out=False)
            page.apply_redactions()
            for r, val in cells:
                fs = min(fontsize_at(page, r), 7.0)
                ins = pymupdf.Rect(r.x0 - 1, r.y0 - 0.5, min(RM, r.x1 + 55), r.y1 + 1)
                page.insert_textbox(ins, val, fontname="F0", fontfile=ARIAL,
                                    fontsize=fs, color=(0, 0, 0))
                report.append((pno, "Origine col", "CELL-OK", val))
    if own and apply_mode:
        out = os.path.join(BASE, pdf.replace(".pdf", ".corr.pdf"))
        doc.save(out, garbage=3, deflate=True)
        os.replace(out, os.path.join(BASE, pdf))
    if own:
        doc.close()
    return report


def scelle_origine_cells(doc):
    """Cellules 'Origine' strippées du Tableau A.1 scellé p.49 -> (rect, valeur)."""
    ws = doc[48].get_text("words")
    cells = []
    for w in sorted(ws, key=lambda w: (w[1], w[0])):
        if w[0] > 475 and 55 < w[1] < 700 and w[4] in ("/", "—"):
            cells.append((pymupdf.Rect(w[0] - 1, w[1] - 1, w[2] + 1, w[3] + 1),
                          "v1.0 / v1.3" if w[4] == "/" else "v2.2"))
    return cells


C = [
 dict(kind="del", page=12, seq=["×", "FX(de‐", "vise→USD)"], tag="coût_bout FX"),
 dict(kind="rep", page=31, seq=["000", "×", "FX),"], new="000),", tag="seuil FX)"),
 dict(kind="rep", page=31, seq=["000", "×", "FX", "/"], new="000 /", tag="P_S FX/"),
 dict(kind="rep", page=31, seq=["P_niveau", "×", "FX", "→"], new="P_niveau →", tag="8.12 FX→"),
 dict(kind="rep", page=55, seq=["000", "×", "FX", "/"], new="000 /", tag="A.1 P_S FX/"),
 dict(kind="rep", page=27, seq=["Impact", "η", "×", "σ", "×", "Q", "×", "√L", "(§12,",
      "linéaire", "en", "Q", ";", "exposant", "à", "confirmer", "en", "calibration)",
      ";", "dépend", "de", "la", "taille", "par", "jambe", "Q."],
      new="Glissement résiduel (écart fill vs quote) ; l'impact η × σ × Q × √L est "
          "déjà déduit dans DDD_eff (§12) — ne pas le recompter ici.",
      xr=95, tag="T8.4 glissement"),
 dict(kind="rep", page=29, seq=["(spread", "CFD", "/", "tick", "+", "frais", "du",
      "future)"],
      new="(conv. B : spread + frais future ; conv. A : frais seuls — spread dans DDD)",
      xr=120, tag="§8.3 coût_bout"),
 dict(kind="rep", page=29, seq=["*", "Spread_v(j)", "n'est", "inclus", "qu'en",
      "Convention", "B", "(§8.4)."],
      new="* Spread_v(j) : conv. B seulement ; Glissement_v(j) : résiduel mesuré "
          "(écart fill/quote) — l'impact ησQ√L est déjà dans DDD_eff (§12).",
      ins=pymupdf.Rect(65, 214, 545, 242), tag="§8.3 note"),
 dict(kind="rep", page=9, seq=["net", "de", "frais"], new="bid/ask brut", tag="r(e) gloss."),
 dict(kind="rep", page=23, seq=["effectif", "après", "frais"], new="exécutable (bid/ask)",
      tag="r(e) §6.1"),
 dict(kind="rep", page=23, seq=["net", "de", "frais"], new="bid/ask brut", tag="r(e) §6.2"),
 dict(kind="rep", page=65, seq=["net", "de", "frais", ";"], new="hors commissions ;",
      tag="r(e) code"),
 dict(kind="rep", page=65, seq=["10", "CFD", "collés", "(IC)"], new="34 bouts collés (IC)",
      xr=35, tag="code 10->34"),
 dict(kind="rep", page=65, seq=["//", "CFD", "IC", "Markets"],
      new="// CFD IC (34 bouts v2.2, §2.3)", xr=95, tag="enum comment"),
 dict(kind="rep", page=36, seq=["<", "0,30,"], new="≤ 0,30,", tag="S(h) borne"),
 dict(kind="rep", page=22, seq=["(v2.1", "/", "«", "v1.5", "»", "au", "sens", "de",
      "la", "v1.4.1,", "§19)."], new="(v2.3 ou ultérieure, §19)", tag="§5.4 version"),
 dict(kind="rep", page=44, seq=["Dix", "CFD", "collés"], new="Trente-quatre bouts collés",
      xr=45, tag="règle 1"),
]

S = [
 dict(kind="del", page=7, seq=["×", "FX(de‐", "vise→USD)"], tag="coût_bout FX"),
 dict(kind="rep", page=25, seq=["000", "×", "FX),"], new="000),", tag="seuil FX)"),
 dict(kind="rep", page=25, seq=["000", "×", "FX", "/"], new="000 /", tag="P_S FX/"),
 dict(kind="rep", page=25, seq=["P_niveau", "×", "FX", "→"], new="P_niveau →", tag="8.12 FX→"),
 dict(kind="rep", page=49, seq=["000", "×", "FX", "/"], new="000 /", tag="A.1 P_S FX/"),
 dict(kind="delpart", page=6, seq=["en", "comme", "devises"], idx=[0], tag="« en comme »"),
 dict(kind="rep", page=6, seq=["La", "posait", "un"], new="Le socle posait un",
      tag="« La posait »"),
 dict(kind="rep", page=7, seq=["La", "applique", "à", "ce", "catalogue", "les",
      "règles", "topologiques", "de", "la,", "sans", "les", "modifier"],
      new="La présente version applique à ce catalogue les règles topologiques de "
          "la précédente, sans les modifier", xr=90, tag="« La applique »"),
 dict(kind="rep", page=7, seq=["Statut:", "(dénombrées", "pour"],
      new="Statut : FRONTIÈRE (dénombrées pour", tag="Statut FRONTIÈRE"),
 dict(kind="rep", page=23, seq=["La", "(règle", "7)", "et"], new="La v1.1 (règle 7) et",
      tag="v1.1 règle 7"),
 dict(kind="rep", page=23, seq=["la", "(«", "DDD"], new="la v1.3 (« DDD", tag="v1.3"),
 dict(kind="rep", page=23, seq=["La", "qualifiait", "ce"], new="La v1.4 qualifiait ce",
      xr=10, tag="v1.4"),
 dict(kind="rep", page=23, seq=["La,", "et"], new="La v1.4.1, et", xr=25, tag="v1.4.1"),
 dict(kind="rep", page=23, seq=["donc", "la,", "interdisent"],
      new="donc la v2.0, interdisent", xr=20, tag="v2.0"),
 dict(kind="rep", page=23, seq=["(spread", "CFD", "/", "tick", "+", "frais", "du",
      "future)"],
      new="(conv. B : spread + frais future ; conv. A : frais seuls — spread dans DDD)",
      xr=90, tag="§8.3 coût_bout"),
 dict(kind="rep", page=23, seq=["*", "Spread_v(j)", "n'est", "inclus", "qu'en",
      "Convention", "B", "(§8.4)."],
      new="* Spread_v(j) : conv. B seulement ; Glissement_v(j) : résiduel mesuré "
          "(écart fill/quote) — l'impact ησQ√L est déjà dans DDD_eff (§12).",
      ins=pymupdf.Rect(65, 163, 545, 191), tag="§8.3 note"),
 dict(kind="rep", page=21, seq=["Impact", "η", "×", "σ", "×", "Q", "×", "√L", "(§12,",
      "linéaire", "en", "Q;", "exposant", "à", "confirmer", "en", "calibration);",
      "dépend", "de", "la", "taille", "par", "jambe", "Q."],
      new="Glissement résiduel (écart fill vs quote) ; l'impact η × σ × Q × √L est "
          "déjà déduit dans DDD_eff (§12) — ne pas le recompter ici.",
      xr=95, tag="T8.4 glissement"),
 dict(kind="rep", page=24, seq=["(:", "−21,88,", "valeur"], new="(v1.1 : −21,88, valeur",
      tag="v1.1 espérance"),
 dict(kind="rep", page=24, seq=["par", "la,", "F05)."], new="par la v1.4.1, F05).",
      xr=25, tag="v1.4.1 F05"),
 dict(kind="rep", page=24, seq=["(:", "+8,09)"], new="(v1.1 : +8,09)", xr=30,
      tag="v1.1 +8,09"),
 dict(kind="rep", page=24, seq=["(", "simulé:", "−21,88)"], new="(v1.1 simulé : −21,88)",
      xr=20, tag="v1.1 simulé"),
 dict(kind="rep", page=24, seq=["(", "§8).", "Les", "valeurs"], new="(v1.1 §8). Les valeurs",
      tag="v1.1 §8 T8.10"),
 dict(kind="rep", page=24, seq=["(", "§8,", "simulation).", "La"],
      new="(v1.1 §8, simulation). La", xr=10, tag="v1.1 §8 T8.11"),
 dict(kind="rep", page=4, seq=["net", "de", "frais"], new="bid/ask brut", tag="r(e) gloss."),
 dict(kind="rep", page=18, seq=["net", "de", "frais"], new="bid/ask brut", tag="r(e) §6.2"),
 dict(kind="rep", page=54, seq=["net", "de", "frais;"], new="hors commissions ;",
      tag="r(e) code"),
 dict(kind="rep", page=54, seq=["10", "CFD", "collés", "(IC)"], new="34 bouts collés (IC)",
      xr=35, tag="code 10->34"),
 dict(kind="rep", page=30, seq=["<", "0,30,"], new="≤ 0,30,", tag="S(h) borne"),
 dict(kind="rep", page=38, seq=["Dix", "CFD", "collés"], new="Trente-quatre bouts collés",
      xr=45, tag="règle 1"),
 dict(kind="rep", page=69, seq=["Fin", "de", "la", "Constitution", "méthodologique",
      "unifiée", "—", "Document", "scellé", "—", "Octobre", "2026", "—", "Usage",
      "interne."],
      new="Fin du Document scellé — Arbitrage FX à chemins ouverts — Octobre 2026 — "
          "Usage interne.", xr=80, tag="Fin de doc"),
 dict(kind="rep", page=32, seq=["(C15)."], new="(C15, cf. Annexe C Const. v2.2).",
      xr=70, tag="C15 renvoi"),
 dict(kind="rep", page=44, seq=["(Annexe", "C,", "C15–C31),"],
      new="(Annexe C de la Const. v2.2, C15–C31),", xr=70, tag="Annexe C renvoi"),
]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    app = mode == "apply"
    print("=== CONSTITUTION ===")
    rep = run(CONST, C, app)
    print("=== SCELLE ===")
    doc = pymupdf.open(os.path.join(BASE, SCELLE))
    cells = {49: scelle_origine_cells(doc)}
    rep += run(SCELLE, S, app, doc=doc, extra_cells=cells)
    if app:
        out = os.path.join(BASE, SCELLE.replace(".pdf", ".corr.pdf"))
        doc.save(out, garbage=3, deflate=True)
        os.replace(out, os.path.join(BASE, SCELLE))
    doc.close()
    print("--- résumé ---")
    for pno, tag, st, x in rep:
        print(f"  p{pno:>3} [{tag}] {st} {x[:58]}")
    print(f"Cellules Origine p.49 : {len(cells[49])}")

if __name__ == "__main__":
    main()
