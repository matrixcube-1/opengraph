#!/usr/bin/env python3
"""Recalibrage cTrader sur journal reel multi-heures.

Pour un journal OgTrade/OgDetect (ticks e/ts/bid/ask), produit :
  1. gaps inter-ticks par symbole + par tranche horaire -> fresh_ms
  2. distribution des spreads vs mediane -> spread_cap
  3. distribution du net detecte par heure -> ou se concentre l'edge
  4. replay exec aux seuils ddd -> choix du seuil sur donnees reelles

Usage : python recalib_jour.py <journal.jsonl> [lat_ms=350]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import opengraph  # noqa: E402
from poc_engine import FX  # noqa: E402

P = dict(fresh_ms=8000.0, spread_cap=1.76, comm_scale=1.10,
         lat_ms=350.0, tau_slip_bp=2.08, q_min=0.5, cooldown_ms=2000)


def hour_of(ts):  # heure locale approx (journal ts = epoch ms UTC)
    return (ts // 3_600_000 + 1) % 24   # +1 : UTC->CET hiver


def main():
    src = Path(sys.argv[1])
    if len(sys.argv) > 2:
        P["lat_ms"] = float(sys.argv[2])
    ticks, last_e = [], {}
    gaps = defaultdict(list)
    spr = defaultdict(list)
    for line in open(src):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("ev") != "tick":
            continue
        ts, s = int(e["ts"]), int(e["e"])
        b, a = float(e["bid"]), float(e["ask"])
        ticks.append((ts, s, b, a))
        if s in last_e:
            gaps[s].append(ts - last_e[s])
        last_e[s] = ts
        spr[s].append(a - b)
    ticks.sort()
    print(f"{len(ticks)} ticks | {src.name}")

    # 1) gaps inter-ticks -> fresh_ms
    print("\n=== gaps inter-ticks (ms) par symbole : med / p90 / p99 ===")
    for s in sorted(gaps):
        g = np.array(gaps[s])
        print(f"  {FX[s]:8} {np.median(g):7.0f} {np.percentile(g,90):8.0f}"
              f" {np.percentile(g,99):8.0f}")
    allg = np.concatenate(list(gaps.values()))
    print(f"  TOUS     {np.median(allg):7.0f} {np.percentile(allg,90):8.0f}"
          f" {np.percentile(allg,99):8.0f}   max={allg.max()}")

    # 2) heures -> densite ticks
    by_h = defaultdict(int)
    for ts, s, b, a in ticks:
        by_h[hour_of(ts)] += 1
    print("\n=== ticks par heure (locale) ===")
    for h in sorted(by_h):
        print(f"  {h:02d}h : {by_h[h]}")

    # 3) replay : nets detectes par heure
    eng = opengraph.Engine.live()
    for ts, s, b, a in ticks:
        eng.on_tick(s, ts, b, a, 1.0,
                    P["fresh_ms"], P["spread_cap"], P["comm_scale"])
    tick_ts = {s: np.array([t[0] for t in ticks if t[1] == s])
               for s in range(28)}
    DD = (0.27, 0.4, 0.5, 0.75, 1.0, 1.5)
    lock = {d: np.full(28, -1e18) for d in DD}
    tfree = {d: -1e18 for d in DD}
    trades = {d: [] for d in DD}
    nets_h = defaultdict(list)
    last = -1
    n_eval = 0
    for ts, s, b, a in ticks:
        if ts == last:
            continue
        last = ts
        ev = eng.eval_at(ts, P["fresh_ms"], P["spread_cap"], P["comm_scale"])
        if ev is None:
            continue
        n_eval += 1
        pi, net, ddd = ev
        nets_h[hour_of(ts)].append(net)
        for d in DD:
            if net >= d and ts >= tfree[d]:
                legs = [FX.index(x) for x in eng.legs(pi)]
                if any(lock[d][e2] > ts for e2 in legs):
                    continue
                r = eng.exec_seq(ts, pi, P["lat_ms"], P["tau_slip_bp"],
                                 P["q_min"], False)
                if r is None:
                    continue
                res, pnl, filled, parts, dur = r
                trades[d].append(pnl)
                for e2 in legs:
                    j = int(np.searchsorted(tick_ts[e2], ts, "right"))
                    lock[d][e2] = (tick_ts[e2][j]
                                   if j < len(tick_ts[e2]) else 1e18)
                tfree[d] = ts + max(dur, 1.0) + P["cooldown_ms"]

    print(f"\n=== nets detectes par heure ({n_eval} evals) ===")
    for h in sorted(nets_h):
        n = np.array(nets_h[h])
        print(f"  {h:02d}h : n={len(n):5d} p99={np.percentile(n,99):+.2f}"
              f" max={n.max():+.2f} >0.4:{(n >= 0.4).sum()}")

    print("\n=== replay exec par seuil ddd ===")
    for d in DD:
        tr = np.array(trades[d])
        if len(tr):
            print(f"  ddd={d:4} : {len(tr):3d} seq wr={100*(tr>0).mean():4.0f}%"
                  f" pnl={tr.sum():+7.2f}$ mean={tr.mean():+.2f}")
        else:
            print(f"  ddd={d:4} :   0 seq")


if __name__ == "__main__":
    main()
