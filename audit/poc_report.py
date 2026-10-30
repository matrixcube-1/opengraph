#!/usr/bin/env python3
"""Rapport qualité POC — couverture instrument x jour, ticks, anomalies."""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path("/opt/arb/data")
LEDGER = DATA / "poc_ledger.json"
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]
FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP","EURJPY","EURCHF",
      "EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF",
      "AUDCAD","AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"]

L = json.loads(LEDGER.read_text())
rows = []
for sym in sorted(set(k.split("/")[0] for k in L if not k.startswith("_"))):
    by_day = defaultdict(lambda: [0, 0, 0, 0])   # fichiers, ticks, bad_px, bad_seq
    for k, v in L.items():
        if k.startswith("_") or not k.startswith(sym + "/"):
            continue
        _, d, h = k.split("/")
        if v.get("ok"):
            by_day[d][0] += 1
            by_day[d][1] += v.get("ticks", 0)
            by_day[d][2] += v.get("bad_px", 0)
            by_day[d][3] += v.get("bad_seq", 0)
    tot_t = sum(v[1] for v in by_day.values())
    rows.append((sym, by_day, tot_t))

print(f"{'instrument':14s} " + " ".join(f"{d[-5:]:>10s}" for d in DAYS) + f" {'ticks':>9s} {'bad':>5s}")
n_fx_ok = 0
for sym, by_day, tot_t in rows:
    cells = []
    for d in DAYS:
        f_, t_, bp, bs = by_day[d]
        cells.append(f"{f_:>2d}f/{t_:>6d}" if f_ else f"{'—':>10s}")
    bad = sum(v[2] + v[3] for v in by_day.values())
    print(f"{sym:14s} " + " ".join(cells) + f" {tot_t:>9,} {bad:>5d}")
    if sym in FX and tot_t > 10000:
        n_fx_ok += 1
print(f"\n{n_fx_ok}/28 paires FX exploitables (>10k ticks) | {len(rows)}/60 instruments vus")
warn = L.get("_warn")
if warn:
    print("WARNINGS:", warn)

if "--deep" in sys.argv:
    # validation post-hoc des CSV : protocole bid/ask + volumes + bornes ms
    import numpy as np
    print("\n=== validation profonde des CSV (deep) ===")
    print(f"{'fichier':50s} {'ticks':>7s} {'bad_px':>6s} {'bad_ms':>6s} {'oob':>4s} {'vol0%':>6s}")
    worst = []
    for csv in sorted(DATA.glob("*/*/*.csv")):
        a = np.loadtxt(csv, delimiter=",", skiprows=1)
        if a.ndim == 1:
            a = a[None, :]
        n = len(a)
        if n == 0:
            continue
        ms, ask, bid, av, bv = a.T
        bad_px = int((bid > ask).sum())
        bad_ms = int((np.diff(ms) <= 0).sum())
        oob = int(((ms < 0) | (ms >= 3_600_000)).sum())
        vol0 = float(((av <= 0) | (bv <= 0)).mean() * 100)
        if bad_px or bad_ms or oob or vol0 > 50:
            rel = csv.relative_to(DATA)
            print(f"{str(rel):50s} {n:>7d} {bad_px:>6d} {bad_ms:>6d} {oob:>4d} {vol0:>5.1f}%")
            worst.append((str(rel), bad_px, bad_ms, oob, vol0))
    if not worst:
        print("aucune anomalie : bid<=ask, ms croissants bornés [0,3.6e6), volumes>0")
    else:
        print(f"{len(worst)} fichier(s) avec anomalies")
