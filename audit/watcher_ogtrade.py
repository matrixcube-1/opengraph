#!/usr/bin/env python3
"""Watcher nuit OgTrade — snapshot horaire dans watcher_status.txt.
Tout evenement non-tick (opportunity/leg_*/settle/system) est recopie
tel quel ; chaque heure, un resume cumulatif est ajoute.
"""
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

JDIR = Path(r"C:\arb_og_trade")
OUT = Path(r"C:\arb_og_trade\watcher_status.txt")
SNAP_S = 3600


def journal_path():
    return JDIR / f"{date.today():%Y-%m-%d}.jsonl"


def main():
    out = open(OUT, "a", encoding="utf-8")
    pos = 0
    last_snap = 0
    ticks = opps = settles = 0
    pnl = 0.0
    while True:
        jp = journal_path()
        if jp.exists():
            with open(jp) as f:
                f.seek(pos)
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    ev = e.get("ev")
                    if ev == "tick":
                        ticks += 1
                        continue
                    ts = e.get("ts", 0)
                    hh = (datetime.fromtimestamp(ts / 1000)
                          .strftime("%m-%d %H:%M:%S") if ts else "?")
                    out.write(f"{hh}  {json.dumps(e, ensure_ascii=False)}\n")
                    if ev == "opportunity":
                        opps += 1
                    elif ev == "settle":
                        settles += 1
                        pnl += e.get("pnl_usd", 0.0)
                pos = f.tell()
        now = time.time()
        if now - last_snap >= SNAP_S:
            last_snap = now
            out.write(f"== SNAP {datetime.now():%H:%M:%S} | ticks={ticks}"
                      f" opps={opps} settles={settles} pnl={pnl:+.2f}$\n")
            out.flush()
        out.flush()
        time.sleep(15)


if __name__ == "__main__":
    main()
