#!/usr/bin/env python3
"""Parite via le binding pyo3 : rejoue les memes points d'evaluation que
`parity_ref.py` / `examples/parity.rs` mais a travers le module `opengraph`
compile --release (maturin). Verifie que le franchissement Python->Rust ne
change pas l'arithmetique (rel_tol 1e-9 via parity_cmp.py).

Usage: python parity_binding.py <fixture_dir> <out.json>
       python parity_cmp.py parity_ref.json <out.json>
"""
import calendar
import json
import sys
import time
from pathlib import Path

import opengraph

FIX = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2])

DAYS = ["2026-10-22", "2026-10-23"]
# Base UTC civile (calendar.timegm) — identique a `epoch_ms` du loader Rust.
# (time.mktime donne la base locale du harnais : -1 h sur cette machine.)
EPOCH = {d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000 for d in DAYS}

eng = opengraph.Engine(str(FIX), DAYS)
assert eng.n_paths() == 2072

e1 = EPOCH[DAYS[1]]
tests_t = [
    EPOCH[DAYS[0]] + 6 * 3_600_000 + 60_000,
    e1 + 7 * 3_600_000 + 15_000,
    e1 + 7 * 3_600_000 + 30_000,
    e1 + 7 * 3_600_000 + 45_000,
    e1 + 7 * 3_600_000 + 90_000,
]

items = []
for t in tests_t:
    ev = eng.eval_at(t, 500.0, 1.7, 1.0)
    if ev is None:
        items.append({"t": t, "none": True})
        continue
    pi, net, ddd = ev
    items.append({"t": t, "net": net, "ddd": ddd, "legs": eng.legs(pi), "start": eng.start(pi)})
    for lat in (1.3, 2.5, 5.0):
        r = eng.exec_seq(t, pi, lat, 0.8, 0.5, False)  # vol_guard=False : parite stricte POC
        if r is None:
            items.append({"exec": {"t": t, "lat": lat, "res": None}})
        else:
            res, pnl, fills, parts, dur = r
            items.append({"exec": {"t": t, "lat": lat, "res": res, "pnl": pnl,
                                   "fills": fills, "parts": parts, "dur": dur}})

with open(OUT, "w") as fh:
    json.dump({"evals": items}, fh, indent=1)
print(f"-> {OUT}")
