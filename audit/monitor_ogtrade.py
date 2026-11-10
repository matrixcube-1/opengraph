#!/usr/bin/env python3
"""Monitoring du run demo OgTrade — lit le journal jsonl et resume.

Usage : python monitor_ogtrade.py [journal.jsonl]
Defaut : C:\\arb_og_trade\\<aujourd'hui>.jsonl
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

JDIR = Path(r"C:\arb_og_trade")


def main():
    src = (Path(sys.argv[1]) if len(sys.argv) > 1
           else JDIR / f"{date.today():%Y-%m-%d}.jsonl")
    seqs = defaultdict(lambda: {"legs": [], "unwinds": 0, "settle": None})
    boots = stops = 0
    net_at_opp = {}
    ticks = 0
    evs = Counter()
    for line in open(src):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("ev")
        evs[ev] += 1
        t0 = e.get("t0")
        if ev == "system":
            if e.get("msg", "").startswith("boot"):
                boots += 1
            elif e.get("msg") == "stop":
                stops += 1
        elif ev == "tick":
            ticks += 1
        elif ev == "opportunity":
            net_at_opp[t0] = e.get("net_usd")
        elif ev in ("leg_send", "leg_fill", "leg_rej"):
            seqs[t0]["legs"].append(e)
        elif ev == "unwind_fill":
            seqs[t0]["unwinds"] += 1
        elif ev == "settle":
            seqs[t0]["settle"] = e

    print(f"journal {src.name} | {ticks} ticks | boots={boots} stops={stops}")
    print(f"events : {dict(evs)}")
    n_done = n_abort = 0
    pnl_tot = 0.0
    gaps = []
    print("\n=== sequences ===")
    for t0, s in sorted(seqs.items()):
        if s["settle"] is None:
            continue
        st = s["settle"]
        ab = st.get("aborted", False)
        pnl = st.get("pnl_usd", 0.0)
        pnl_tot += pnl
        n_abort += ab
        n_done += (not ab)
        net = net_at_opp.get(t0)
        gap = (pnl - net) if net is not None else None
        if gap is not None:
            gaps.append(gap)
        sym = [l.get("sym") or f"e{l.get('e')}" for l in s["legs"]
               if l["ev"] == "leg_send"]
        print(f"  {t0}: {'ABORT' if ab else 'done':5} pnl={pnl:+.2f}$"
              f" net={net if net is None else f'{net:+.2f}'}"
              f" gap={gap if gap is None else f'{gap:+.2f}'}"
              f" legs={len(sym)}/{s['unwinds']} {sym}")

    print(f"\n=== total : {n_done} done, {n_abort} abort,"
          f" pnl={pnl_tot:+.2f}$ ===")
    if gaps:
        gaps.sort()
        n = len(gaps)
        print(f"ecart realise-detecte : med={gaps[n//2]:+.2f}$"
              f" min={gaps[0]:+.2f} max={gaps[-1]:+.2f}")
        print("(gap ~0 attendu : le modele corrigé predit le cout reel)")


if __name__ == "__main__":
    main()
