# Addendum R2 — protocole d'exécution séquentielle non-atomique
# Ajoute une page d'addendum en fin de chaque PDF + renvois croisés
# (§1 critère 2, intro pseudo-code). Usage : python add_addendum_r2.py [apply]
import os, sys, pymupdf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARI  = 'C:/Windows/Fonts/arial.ttf'
ARIB = 'C:/Windows/Fonts/arialbd.ttf'
SEG  = 'C:/Windows/Fonts/seguisym.ttf'
DRY  = 'apply' not in sys.argv

DOCS = [
    dict(file='Constitution_Unifiee_Arbitrage_Chemins_Ouverts_v2.2.pdf',
         p_crit=10,  # page de la règle §1 critère 2
         p_pcode=65, # page de l'intro du pseudo-code
         header_l='Constitution méthodologique unifiée v2.2 — Arbitrage FX à chemins ouverts',
         header_r='Dual Venue CME + IC Markets — Usage interne',
         footer_l='Octobre 2026 — Version 2.2 — Document interne, ne pas diffuser'),
    dict(file='Arbitrage_FX_Chemins_Ouverts_Document_Scelle.pdf',
         p_crit=5,
         p_pcode=54,
         header_l='Arbitrage FX à chemins ouverts — Dual Venue CME + IC Markets',
         header_r='Dual Venue CME + IC Markets — Usage interne',
         footer_l='Octobre 2026 — Document scellé — Usage interne'),
]

OLD_CRIT = "2. l'exécution est quasi simultanée, la position résiduelle étant un sous-produit et non un pari additionnel"
NEW_CRIT = "2. l'exécution est séquentielle et quasi immédiate (protocole : Addendum R2) ; la position résiduelle est un sous-produit, non un pari"
PCODE_TAIL = ' — voir Addendum R2.'

TITRE = 'ADDENDUM R2 — EXÉCUTION SÉQUENTIELLE NON-ATOMIQUE DES CHEMINS OUVERTS'
CORPS = """Portée. Ajout postérieur aux corrections R2, intégré au corpus normatif. Il précise le critère 2 du §1 (l'exécution est séquentielle et quasi immédiate, non atomique) et complète la règle d'envoi du §8.5 et le pseudo-code de référence. Un chemin ouvert ne peut pas être exécuté atomiquement : chaque jambe est un fill réel ; le présent protocole fixe ce qui se produit entre deux fills, jusqu'à quatre fills au total (MAX_LEGS = 3 ; croisé synthétique CME = 2 fills ; ou 3 jambes + bout CFD terminal).

E1 — Ordres. Chaque jambe part en ordre limite agressif IOC (immediate-or-cancel) au dernier prix vérifié ± tolérance τ_slip ; le reliquat non rempli est tué instantanément. Aucun ordre posé (resting). Quantités : contrats entiers au CME ; min-lot chez IC Markets. Ordre : celui du chemin ; dans un croisé synthétique CME, la jambe la moins liquide part la première (le fill le plus risqué se tente à plat).

E2 — Admission de la séquence. Le filtre send() du §8.5 est inchangé ; s'ajoute : la durée de vie prédite de l'écart (S(h), Hawkes §10) doit couvrir T_seq = Σ latences estimées par fill × m_seq, m_seq ≥ 1 calibrée, latences mesurées par venue (§11).

E3 — Vérifications avant chaque fill k > 1. (a) fraîcheur de la quote : âge ≤ τ_fresh ; (b) spread ≤ 1,7 × normal/jambe ; (c) DDD_eff du chemin résiduel (jambes k..n) recalculé aux marks et à la quantité courants. Tout échec → abort (E6).

E4 — Continuation. Continuer si et seulement si l'espérance du net résiduel sur les jambes k..n ≥ −(C_unwind + m_abort), où C_unwind est le coût certain du débouclage immédiat des jambes remplies (aller-retour de spread + frais mesurés) ; m_abort ≥ 0 calibrée.

E5 — Fill partiel. Le reliquat est tué immédiatement (jamais de poursuite). Si q_fill ≥ q_min calibré, les quantités aval restent valides (arrondi aux contrats entiers → rounding_residual du ddd_exec) et DDD_eff(q_fill) > Coût_total(q_fill) recalculés, la séquence continue à q_fill. Sinon → E6.

E6 — Abort = unwind to flat. Débouclage immédiat au marché des jambes déjà remplies, en ordre inverse des fills. Coût enregistré = aller-retour de spread réalisé + frais. Débouclage rejeté : retenté ≤ N_u fois puis alerte manuelle ; la position passe en résiduel non prévu (E7).

E7 — Résiduels. Résiduel prévu (chemin complet) : inchangé — résorption sur l'horizon h (S(h), H, §10). Résiduel non prévu (E6 échoué) : marqué, surveillé en continu, débouclé au premier prix acceptable ; il n'entre pas dans les statistiques de résorption.

E8 — Journal par fill. Heure de la quote, heure du fill, q demandée / remplie, prix, DDD recalculé ; par abort : cause (E3 a/b/c, E5, rejet courtier), jambes remplies, C_unwind réalisé. C'est la base de mesure de la fréquence et du coût des abandons.

E9 — Calibration. Paramètres optimisés par Optuna : τ_fresh, τ_slip, m_seq, q_min, m_abort, N_u. Objectif = P&L réalisable : fills complets × net − abandons × C_unwind − coût des résiduels non prévus. Le Monte Carlo rejoue la séquence fill par fill : chaque fill est conditionné à la survie de la quote à son heure d'exécution ; un chemin non exécutable dans sa durée de vie prédite ne compte pas comme un gain."""


def line_rect(page, needle):
    """Rect de la ligne contenant `needle` + taille de police estimée."""
    ws = page.get_text('words')
    for w in ws:
        if needle in w[4]:
            y0 = w[1]
            line = [x for x in ws if abs(x[1] - y0) < 2]
            return pymupdf.Rect(min(x[0] for x in line), min(x[1] for x in line),
                                max(x[2] for x in line), max(x[3] for x in line))
    return None


def main():
    for d in DOCS:
        path = os.path.join(BASE, d['file'])
        doc = pymupdf.open(path)
        print('=' * 10, d['file'][:50])

        # --- 1) réécriture du critère 2 (§1) ---
        pg = doc[d['p_crit'] - 1]
        r = line_rect(pg, 'simultanée')
        if r is None:
            print('  MISS critère 2')
        else:
            print(f'  critère2 p{d["p_crit"]}: {r}')
            if not DRY:
                pg.add_redact_annot(r, fill=(1, 1, 1), cross_out=False)
                pg.apply_redactions()
                rc = pg.insert_textbox(pymupdf.Rect(r.x0, r.y0 - 1, r.x1 + 40, r.y1 + 4),
                                       NEW_CRIT + ' ;', fontname='FA', fontfile=ARI,
                                       fontsize=8.9, color=(0, 0, 0))
                print(f'    reinsert rc={rc:.1f}', 'OK' if rc >= 0 else 'ECHEC')

        # --- 2) renvoi en fin de ligne d'intro pseudo-code ---
        pg = doc[d['p_pcode'] - 1]
        r = line_rect(pg, 'exécute')
        if r is None:
            print('  MISS pseudo-code')
        else:
            ins = pymupdf.Rect(r.x1 + 2, r.y0 - 1, min(r.x1 + 200, 563), r.y1 + 3)
            print(f'  pcode p{d["p_pcode"]}: fin x={r.x1:.0f} ins={ins}')
            if not DRY:
                rc = pg.insert_textbox(ins, PCODE_TAIL, fontname='FA', fontfile=ARI,
                                       fontsize=8.9, color=(0, 0, 0))
                print(f'    reinsert rc={rc:.1f}', 'OK' if rc >= 0 else 'ECHEC')

        # --- 3) page d'addendum en fin de document ---
        if not DRY:
            p = doc.new_page(width=595.27, height=841.89)
            n = doc.page_count
            p.insert_text((50, 32), d['header_l'], fontname='FA', fontfile=ARI, fontsize=7.5)
            p.insert_text((330, 32), d['header_r'], fontname='FA', fontfile=ARI, fontsize=7.5)
            p.insert_text((60, 82), TITRE, fontname='FB', fontfile=ARIB, fontsize=11.5)
            rc = p.insert_textbox(pymupdf.Rect(60, 100, 560, 780), CORPS,
                                  fontname='FSYM', fontfile=SEG, fontsize=9.0,
                                  color=(0, 0, 0))
            print(f'  addendum p{n}: corps rc={rc:.1f}', 'OK' if rc >= 0 else 'ECHEC')
            p.insert_text((50, 818), d['footer_l'] + '  |  Addendum R2', fontname='FA',
                          fontfile=ARI, fontsize=7.5)
            p.insert_text((528, 818), f'Page {n} / {n}', fontname='FA', fontfile=ARI, fontsize=7.5)

        if not DRY:
            out = path.replace('.pdf', '.add.pdf')
            doc.save(out, garbage=3, deflate=True)
            print('  ->', os.path.basename(out))
        doc.close()


if __name__ == '__main__':
    main()
