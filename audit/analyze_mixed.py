#!/usr/bin/env python3
"""Analyse cand_mixed.npz : part des chemins mixtes (sauts métal) dans
l'argmax des candidats, par jour, + exécution simulée des meilleurs."""
import sys
import time

import numpy as np

from poc_engine import Engine, FX, CURR, METAL_SYM, EPOCH, DAYS


def path_repr(p):
    out = []
    for e in p:
        if isinstance(e, tuple):
            if e[0] == "X":
                out.append(f"X{e[1]}->{CURR[e[2]]}")
            else:
                out.append(f"B:{e[1]}")
        else:
            out.append(FX[e])
    return " ".join(out)


def day_of(t_ms):
    for d, ep in EPOCH.items():
        if ep <= t_ms < ep + 86_400_000:
            return d
    return "?"


def main():
    e = Engine(mixed=True, verbose=False)
    z = np.load("/opt/arb/poc/cand_mixed.npz")
    cands = z["cands"]
    if len(cands) == 0:
        print("aucun candidat")
        return
    pis = cands[:, 1].astype(int)
    ism = e.P_ISM.any(1)
    is_mixed = ism[pis]
    n = len(cands)
    m = int(is_mixed.sum())
    print(f"{n:,} candidats : {m:,} mixtes ({100*m/n:.2f}%) | {n-m:,} FX")

    # par jour
    days = {}
    for r in cands:
        days.setdefault(day_of(int(r[0])), []).append(r)
    for d in sorted(days):
        rows = np.array(days[d])
        mx = ism[rows[:, 1].astype(int)]
        nm = int(mx.sum())
        print(f"  {d} : {len(rows):,} cands | {nm} mixtes ({100*nm/len(rows):.2f}%)"
              f" | net max FX {rows[~mx,2].max() if (~mx).any() else float('nan'):.2f}"
              f" | net max mix {rows[mx,2].max() if mx.any() else float('nan'):.2f}")

    # épisodes
    eps = z["episodes"]
    if len(eps):
        ep_mixed = ism[eps[:, 2].astype(int)]
        print(f"épisodes : {len(eps)} | mixtes {int(ep_mixed.sum())}")

    # top mixtes
    mix = cands[is_mixed]
    if len(mix) == 0:
        print("\naucun candidat mixte -> les sauts ne gagnent jamais l'argmax")
        return
    top = mix[np.argsort(mix[:, 2])[::-1][:15]]
    print(f"\ntop {len(top)} mixtes (net détection):")
    for t_ms, pi, net, ddd in top:
        print(f"  {day_of(int(t_ms))} t={int(t_ms)} net={net:8.2f} ddd={ddd:8.2f} "
              f"pi={int(pi)} {path_repr(e.paths[int(pi)])}")

    # exécution réaliste des 5 meilleurs (P figé : ddd 2.5, IOC tol 2.4bp)
    print("\nexec_seq (lat=5ms, tau=2.4bp, q_min=0.5):")
    for t_ms, pi, net, _ in top[:5]:
        r = e.exec_seq(int(t_ms), int(pi), 5, 24.0, 0.5)
        if r is None:
            print(f"  pi={int(pi)} -> exec impossible (données)")
        else:
            print(f"  pi={int(pi)} {r['res']:5s} pnl={r['pnl']:8.2f} "
                  f"fills={r['fills']} parts={r['parts']} why={r['why']}")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n({time.time()-t0:.0f}s)")
