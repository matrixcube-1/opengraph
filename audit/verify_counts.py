# Verification independante v2 — semantique corrigee:
#  - chemins devises: 1..L jambes, sans repetition, sur K8 dirige
#  - bout (CFD/future): arete finale optionnelle apres 0..L jambes (hors budget L)
#  - feuille (devise): jambe terminale comptant dans L, puis indice eventuel (arete bout)
import itertools

CORE = ['EUR','GBP','JPY','USD','AUD','CAD','CHF','NZD']

def count_paths(c_n, leaf_bouts, L, use_leaves=True):
    LEAVES = list(leaf_bouts.keys()) if use_leaves else []
    total = 0
    for s in CORE:
        stack = [(s, frozenset([s]), 0)]
        while stack:
            node, vis, k = stack.pop()
            if k >= 1:
                total += 1                    # chemin devise
            total += c_n.get(node, 0)         # extension vers bout de node (0..L jambes)
            if k < L:
                for f in LEAVES:
                    total += 1                # jambe terminale vers feuille
                    total += leaf_bouts.get(f, 0)
                for nxt in CORE:
                    if nxt not in vis:
                        stack.append((nxt, vis | {nxt}, k + 1))
    return total

def closed_form(c_n, leaf_bouts, L, use_leaves=True):
    V = 8
    A = 0; t = 1
    for k in range(1, L+1):
        t *= (V-1) - (k-1); A += t
    B = 0; t = 1
    for k in range(1, L+1):
        B += t; t *= (V-2) - (k-1)
    Lam = 1; t = 1
    for k in range(1, L):
        t *= (V-1) - (k-1); Lam += t
    C = sum(c_n.values())
    n_f = len(leaf_bouts) if use_leaves else 0
    C_f = sum(leaf_bouts.values()) if use_leaves else 0
    return V*A + C*(1+(V-1)*B) + (n_f + C_f)*V*Lam, dict(A=A,B=B,Lam=Lam)

cases = [
    ("U0 10CFD L3", {'EUR':3,'GBP':1,'JPY':1,'USD':4,'AUD':1}, {}, 3, False, 4672),
    ("U0 10CFD L4", {'EUR':3,'GBP':1,'JPY':1,'USD':4,'AUD':1}, {}, 4, False, 19792),
    ("Intra-CME L3", {'USD':4}, {}, 3, False, 3112),
    ("U1-S L3", {'EUR':1,'USD':5}, {}, 3, False, 3632),
    ("U1-S L4", {'EUR':1,'USD':5}, {}, 4, False, 15392),
    ("U1-N L3", {'EUR':7,'GBP':2,'JPY':2,'USD':19,'AUD':3,'CHF':1}, {}, 3, False, 10912),
    ("U1-N L4", {'EUR':7,'GBP':2,'JPY':2,'USD':19,'AUD':3,'CHF':1}, {}, 4, False, 46192),
    ("U1-ST L3", {'EUR':9,'GBP':2,'JPY':2,'USD':21,'AUD':3,'CHF':2,'CAD':1}, {}, 3, False, 12472),
    ("U1-ST L4", {'EUR':9,'GBP':2,'JPY':2,'USD':21,'AUD':3,'CHF':2,'CAD':1}, {}, 4, False, 52792),
    ("U2 L3", {'EUR':9,'GBP':2,'JPY':2,'USD':21,'AUD':3,'CHF':2,'CAD':1},
        {'HKD':2,'NOK':1,'SEK':1,'ZAR':1}, 3, True, 16072),
    ("U2 L4", {'EUR':9,'GBP':2,'JPY':2,'USD':21,'AUD':3,'CHF':2,'CAD':1},
        {'HKD':2,'NOK':1,'SEK':1,'ZAR':1}, 4, True, 71512),
]

print(f"{'cas':18} {'enum':>8} {'close':>8} {'attendu':>8}  match")
n_ok = 0
for name, cn, lb, L, leaves, exp in cases:
    enum = count_paths(cn, lb, L, leaves)
    close, coef = closed_form(cn, lb, L, leaves)
    ok = (enum == close == exp)
    n_ok += ok
    print(f"{name:18} {enum:>8} {close:>8} {exp:>8}  {ok}")
print(f"\n{n_ok}/{len(cases)} cas reproduits")

# detail par graphe pour U0 (verifier colonnes 262/334/226/370)
cn = {'EUR':3,'GBP':1,'JPY':1,'USD':4,'AUD':1}
print("\nU0 L3 par graphe (attendu: EUR 521 GBP 593 JPY 593 USD 485 AUD 593 CAD 629 CHF 629 NZD 629)")
for s in CORE:
    LEAVES=[]
    t=0
    stack=[(s,frozenset([s]),0)]
    while stack:
        node,vis,k=stack.pop()
        if k>=1: t+=1
        t+=cn.get(node,0)
        if k<3:
            for nxt in CORE:
                if nxt not in vis: stack.append((nxt,vis|{nxt},k+1))
    print(f"  {s}-ouvert: {t}")
