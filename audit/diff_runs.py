#!/usr/bin/env python3
"""Comparaison differentielle entre deux runs nt_realism/nt_strategy —
attribution de chaque ecart au reglage qui separe les deux executions.

Apparie les candidats par (t, legs, start) :

- evals : le candidat a-t-il ete traite des deux cotes ? Les absents
  signalent un effet d'occupation (t_free/in_flight couvre des
  fenetres differentes selon la duree d'execution).
- execs : res, pnl, fills, parts, dur sur les candidats communs ;
  matrice res_a -> res_b pour les flips de decision.

Usage: diff_runs.py ref.json run.json [label]
"""
import json
import sys
from collections import Counter

A = json.load(open(sys.argv[1]))["evals"]
B = json.load(open(sys.argv[2]))["evals"]
LBL = sys.argv[3] if len(sys.argv) > 3 else "diff"
REL_TOL = 1e-9


def candidates(recs):
    """Candidats ordonnes : key -> (eval, exec|None)."""
    out, dup = {}, Counter()
    for e in recs:
        if "exec" in e:
            out[last][1] = e["exec"]
            continue
        key = (e["t"], ",".join(e.get("legs", [])), e.get("start", ""))
        dup[key] += 1
        if dup[key] > 1:
            key = (*key, dup[key])
        out[key] = [e, None]
        last = key
    return out


ca, cb = candidates(A), candidates(B)
ka, kb = set(ca), set(cb)
common = sorted(ka & kb, key=lambda k: k[0])

print(f"=== {LBL} ===")
print(f"candidats : A={len(ka)} B={len(cb)} communs={len(common)} "
      f"| seulement A={len(ka-kb)} seulement B={len(kb-ka)}")

if ka - kb:
    ex_only_a = sum(1 for k in ka - kb if ca[k][1])
    print(f"  dont execs seulement A : {ex_only_a} "
          f"(bloques par occupation cote B)")
if kb - ka:
    ex_only_b = sum(1 for k in kb - ka if cb[k][1])
    print(f"  dont execs seulement B : {ex_only_b}")

# --- divergences d'eval (doivent etre ~0 : meme moteur, memes gates) ---
ev_mis = 0
for k in common:
    ea, eb = ca[k][0], cb[k][0]
    if ea.get("none") or eb.get("none"):
        if not (ea.get("none") and eb.get("none")):
            ev_mis += 1
        continue
    for f in ("net", "ddd"):
        if abs(ea[f] - eb[f]) / max(abs(ea[f]), abs(eb[f]), 1e-12) > REL_TOL:
            ev_mis += 1
print(f"evals divergentes : {ev_mis}")

# --- execs sur candidats communs ---
flips = Counter()
both_done = []
sum_a = sum_b = 0.0
n_ex = 0
for k in common:
    xa, xb = ca[k][1], cb[k][1]
    if xa is None or xb is None:
        continue
    n_ex += 1
    ra, rb = xa["res"], xb["res"]
    if ra != rb:
        flips[(ra, rb)] += 1
    pa = xa.get("pnl")
    pb = xb.get("pnl")
    if pa is not None:
        sum_a += pa
    if pb is not None:
        sum_b += pb
    if ra == "done" and rb == "done":
        both_done.append((k, pa, pb,
                          xa.get("dur", 0), xb.get("dur", 0)))

print(f"execs apparies : {n_ex} | flips res : {sum(flips.values())}")
for (ra, rb), n in flips.most_common(8):
    print(f"    {ra!r:>22} -> {rb!r:<22} : {n}")

if both_done:
    dpnl = sorted(pb - pa for _, pa, pb, *_ in both_done)
    n = len(dpnl)
    q = lambda p: dpnl[min(n - 1, int(p * n))]
    maxabs = max(dpnl, key=abs)
    print(f"both done : {n} | dPnl(B-A) : mean={sum(dpnl)/n:+.4f} "
          f"med={q(0.5):+.4f} p95={q(0.95):+.4f} min={dpnl[0]:+.4f} "
          f"max={dpnl[-1]:+.4f} max|.|={maxabs:+.4f}")
    worst = max(both_done, key=lambda x: abs(x[2] - x[1]))
    print(f"  pire ecart : t={worst[0][0]} {worst[0][1]} "
          f"ref={worst[1]:+.4f} run={worst[2]:+.4f} "
          f"dur {worst[3]}->{worst[4]}")

print(f"PnL total (execs apparies) : A={sum_a:+.2f} B={sum_b:+.2f} "
      f"d={sum_b - sum_a:+.2f}")

# PnL global incluant les execs non apparies (effet occupation)
ta = sum(x["pnl"] for k in ka if (x := ca[k][1]) and x.get("pnl") is not None)
tb = sum(x["pnl"] for k in kb if (x := cb[k][1]) and x.get("pnl") is not None)
print(f"PnL global (tous execs)    : A={ta:+.2f} B={tb:+.2f} "
      f"d={tb - ta:+.2f}")
