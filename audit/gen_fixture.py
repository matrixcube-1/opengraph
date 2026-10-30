#!/usr/bin/env python3
"""Genere un dataset synthetique layout /opt/arb/data pour test de parite
Python poc_engine <-> engine Rust. Ticks Dukas : ms_off,ask,bid,ask_vol,bid_vol.

Deterministe (seed fixe). Une distorsion plantee sur EURJPY jour 2 pour
garantir des evals positives.
"""
import csv
import sys
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "fixture_ticks")
DAYS = ["2026-10-22", "2026-10-23"]

FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP","EURJPY","EURCHF",
      "EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF",
      "AUDCAD","AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"]
CURR = ["USD","EUR","GBP","JPY","CHF","CAD","AUD","NZD"]
CI = {c: i for i, c in enumerate(CURR)}
UP = [1.0, 1.10, 1.30, 1.0/150.0, 1.0/0.90, 1.0/1.35, 0.65, 0.60]

rng = np.random.default_rng(20261027)

for day_i, day in enumerate(DAYS):
    for s in FX:
        a, b = CI[s[:3]], CI[s[3:]]
        mid0 = UP[a] / UP[b]
        # 2 heures de ticks espaces ~300-800 ms (jour 1) et ~250-900 ms (jour 2)
        for h in (6, 7):
            n = 120
            offs = np.cumsum(rng.integers(250, 900, n))
            offs = offs[offs < 3_600_000]
            noise = np.cumsum(rng.normal(0, 4e-5, len(offs)))
            mids = mid0 * (1.0 + noise)
            half = rng.uniform(5e-5, 2.5e-4, len(offs))  # spread relatif variable
            bids = mids * (1 - half)
            asks = mids * (1 + half)
            # distorsion plantee : EURJPY jour 2, heure 7, ticks 40-70 -> ask -0.4%
            if day_i == 1 and s == "EURJPY" and h == 7:
                asks[40:70] *= 0.996
            av = rng.uniform(0.8, 1.5, len(offs))
            bv = rng.uniform(0.8, 1.5, len(offs))
            d = OUT / s / day
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{h:02d}.csv", "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["ms_off", "ask", "bid", "ask_vol", "bid_vol"])
                for i in range(len(offs)):
                    w.writerow([int(offs[i]), f"{asks[i]:.6f}", f"{bids[i]:.6f}",
                                f"{av[i]:.4f}", f"{bv[i]:.4f}"])
print(f"fixture ecrit dans {OUT}")
