#!/usr/bin/env python3
"""Distribution PnL RÉALISÉE (exec_seq) de tous les candidats mixtes ddd>2.5
+ échantillon FX de contrôle. Répond à : les signaux métal survivent-ils
l'exécution séquentielle ?"""
import time

import numpy as np

from poc_engine import Engine, enum_paths

LAT, TAU, QMIN = 5.0, 24.0, 0.5   # paramètres POC usuels


def main():
    e = Engine(mixed=True, verbose=False)
    paths, _ = enum_paths(True)
    ism = np.array([any(isinstance(x, tuple) and x[0] == "X" for x in p)
                    for p in paths])
    c = np.load("/opt/arb/poc/cand_mixed.npz")["cands"]
    sel = c[c[:, 3] > 2.5]
    pis, msk = sel[:, 1].astype(int), ism[sel[:, 1].astype(int)]
    mix = sel[msk]
    fx = sel[~msk]
    rng = np.random.default_rng(7)
    fx_s = fx[rng.choice(len(fx), min(len(mix) * 3, len(fx)), replace=False)]

    def run(rows, label):
        n = len(rows)
        pnls, aborts, dones = [], 0, 0
        for t_ms, pi, net_d, ddd in rows:
            r = e.exec_seq(int(t_ms), int(pi), LAT, TAU, QMIN)
            if r is None:
                continue
            if r["res"] == "done":
                dones += 1
            else:
                aborts += 1
            pnls.append(r["pnl"])
        p = np.array(pnls)
        print(f"{label}: n={n} exec={len(p)} done={dones} abort={aborts} "
              f"({100*aborts/max(len(p),1):.1f}%)")
        if len(p):
            print(f"   pnl: moy={p.mean():7.2f} med={np.median(p):7.2f} "
                  f"p10={np.percentile(p,10):7.2f} p90={np.percentile(p,90):7.2f} "
                  f"sum={p.sum():9.2f} win%={100*(p>0).mean():.1f}")
        return p

    pm = run(mix, "MIXTE ddd>2.5")
    pf = run(fx_s, "FX    ddd>2.5 (x3 ech)")
    np.savez("/opt/arb/poc/exec_mixed_dist.npz", mix=pm, fx=pf)


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n({time.time()-t0:.0f}s)")
