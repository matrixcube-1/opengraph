#!/usr/bin/env python3
"""Replay du corpus réel cTrader (recalib 31 min) sous le moteur corrigé
(coût aller-retour marge, commit 0a4f877).

Pont MC <-> live : le MC valide la méthode sur générateur ; ICI on mesure
le taux d'opportunités RÉELLES qui survivent au coût d'aller-retour sur
la vraie microstructure cTrader (send-on-change, spreads réels).

Params = conditions cBot réelles : fresh 8000, cap 1.76, scale 1.10,
lat 350 ms (fill mesuré), tau 2.08 bp (MaxRange cBot).

Usage : python replay_recalib.py [ticks.jsonl]
"""
import json
import sys
from pathlib import Path

import numpy as np

import opengraph

sys.path.insert(0, str(Path(__file__).parent))
from poc_engine import FX  # noqa: E402

TICKS = (Path(__file__).parent.parent / "ctrader_client" / "journal" /
         "_archive" / "recalib_2026-11-10_16h18-16h49_ticks.jsonl")

P = dict(fresh_ms=8000.0, spread_cap=1.76, comm_scale=1.10,
         lat_ms=350.0, tau_slip_bp=2.08, q_min=0.5, cooldown_ms=2000)


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else TICKS
    ticks = []
    with open(src) as f:
        for line in f:
            e = json.loads(line)
            if e.get("ev") == "tick":
                ticks.append((int(e["ts"]), int(e["e"]),
                              float(e["bid"]), float(e["ask"])))
    ticks.sort()
    print(f"{len(ticks)} ticks, {src.name}")

    eng = opengraph.Engine.live()
    for ts, e, bid, ask in ticks:
        eng.on_tick(e, ts, bid, ask, 1.0,
                    P["fresh_ms"], P["spread_cap"], P["comm_scale"])

    # replay : eval à chaque timestamp unique ; verrou quote-consommée
    # comme mc_realiste ; exec_seq aux seuils
    tick_ts = {e: np.array([t[0] for t in ticks if t[1] == e])
               for e in range(28)}
    lock_until = {d: np.full(28, -1e18)
                  for d in (0.27, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0)}
    nets = []
    trades = {d: [] for d in lock_until}
    t_free = {d: -1e18 for d in lock_until}
    last_t = -1
    for ts, e, bid, ask in ticks:
        if ts == last_t:
            continue
        last_t = ts
        ev = eng.eval_at(ts, P["fresh_ms"], P["spread_cap"], P["comm_scale"])
        if ev is None:
            continue
        pi, net, ddd = ev
        nets.append((ts, net, ddd))
        for d in trades:
            if net >= d and ts >= t_free[d]:
                legs = [FX.index(s) for s in eng.legs(pi)]
                if any(lock_until[d][e2] > ts for e2 in legs):
                    continue
                r = eng.exec_seq(ts, pi, P["lat_ms"], P["tau_slip_bp"],
                                 P["q_min"], False)
                if r is None:
                    continue
                res, pnl, filled, parts, dur = r
                trades[d].append(dict(t=ts, pi=pi, net=net, ddd=ddd,
                                      res=res, pnl=pnl, filled=filled,
                                      dur=dur))
                for e2 in legs:
                    j = int(np.searchsorted(tick_ts[e2], ts, "right"))
                    lock_until[d][e2] = (tick_ts[e2][j]
                                         if j < len(tick_ts[e2]) else 1e18)
                t_free[d] = ts + max(dur, 1.0) + P["cooldown_ms"]

    nets_a = np.array([n[1] for n in nets])
    print(f"\n=== distribution nets détectés (evals={len(nets_a)}) ===")
    if len(nets_a):
        for q in (50, 75, 90, 95, 99, 99.9, 100):
            print(f"  p{q:>4}: {np.percentile(nets_a, q):+.3f} $")
        print(f"  net>0: {(nets_a > 0).sum()}  >0.27: {(nets_a > 0.27).sum()}"
              f"  >1.0: {(nets_a > 1.0).sum()}  >2.0: {(nets_a > 2.0).sum()}")
    for d, tr in trades.items():
        pnls = np.array([t["pnl"] for t in tr])
        print(f"\n=== ddd={d} : {len(tr)} séquences ===")
        if len(tr):
            wr = (pnls > 0).mean()
            print(f"  wr={wr:.0%} pnl={pnls.sum():+.2f}$ "
                  f"mean={pnls.mean():+.2f} min={pnls.min():+.2f} "
                  f"max={pnls.max():+.2f}")
            for t in tr[:10]:
                print(f"    net={t['net']:+.2f} -> {t['res']} pnl={t['pnl']:+.2f}"
                      f" filled={t['filled']} dur={t['dur']:.0f}ms")


if __name__ == "__main__":
    main()
