#!/usr/bin/env python3
"""ticks_view.py — flux de ticks en direct, timestamp par timestamp.

Lit journal/ticks/<date>.jsonl en tail (live uniquement) et affiche
chaque quote : heure d'arrivee locale (ms), symbole, bid, ask, spread bp.

Usage : python -u ticks_view.py [--all] [--pair EURUSD]
"""
import json
import os
import sys
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP","EURJPY","EURCHF",
      "EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF",
      "AUDCAD","AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"]

def main():
    replay_all = "--all" in sys.argv
    pair = None
    for i, a in enumerate(sys.argv):
        if a == "--pair" and i + 1 < len(sys.argv):
            pair = sys.argv[i + 1].upper()

    path = os.path.join(BASE, "journal", "ticks",
                        datetime.date.today().isoformat() + ".jsonl")
    f = open(path, "r", encoding="utf-8", errors="replace")
    if not replay_all:
        f.seek(0, os.SEEK_END)

    print("=" * 64)
    print(" TICKS LIVE — %s | heure arrivee | symbole | bid / ask | bp"
          % (pair or "28 paires"))
    print("=" * 64)
    while True:
        line = f.readline()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("ev") != "tick":
            continue
        sym = FX[ev["e"]] if ev["e"] < len(FX) else "e%d" % ev["e"]
        if pair and sym != pair:
            continue
        t = datetime.datetime.fromtimestamp(ev["ts"] / 1000)
        bid, ask = ev["bid"], ev["ask"]
        bp = (ask - bid) / bid * 1e4 if bid else 0.0
        print("%s.%03d  %-6s  %.5f / %.5f   %4.2f bp"
              % (t.strftime("%H:%M:%S"), t.microsecond // 1000,
                 sym, bid, ask, bp))

if __name__ == "__main__":
    main()
