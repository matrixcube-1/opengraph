#!/usr/bin/env python3
"""Parite : execute le VRAI poc_engine Python sur le fixture synthetique et
dump les resultats de reference en JSON. Le Rust reproduit les memes appels.

Usage: python parity_ref.py <fixture_dir> <out.json>
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import poc_engine

FIX = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2])

# patch des constantes du POC vers le fixture
DAYS = ["2026-10-22", "2026-10-23"]
poc_engine.DATA = FIX
poc_engine.DAYS = DAYS
poc_engine.EPOCH = {d: int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000) - time.timezone * 1000 for d in DAYS}

eng = poc_engine.Engine(syms=poc_engine.FX, verbose=True)
paths, starts = eng.paths, eng.p_start

# points d'evaluation : pendant et apres la distorsion plantee
e1 = poc_engine.EPOCH[DAYS[1]]
tests_t = [
    poc_engine.EPOCH[DAYS[0]] + 6 * 3_600_000 + 60_000,   # jour 1 06:01 (livre coherent)
    e1 + 7 * 3_600_000 + 15_000,                          # jour 2 07:00:15 (dans distorsion?)
    e1 + 7 * 3_600_000 + 30_000,                          # jour 2 07:00:30
    e1 + 7 * 3_600_000 + 45_000,
    e1 + 7 * 3_600_000 + 90_000,                          # apres distorsion
]

res = {"evals": [], "execs": []}
for t in tests_t:
    eng.sync(t)
    ev = eng.eval_at(t, 500.0, 1.7, 1.0, rows=eng.IDX_FX)
    if ev is None:
        res["evals"].append({"t": t, "none": True})
        continue
    net, pi, ddd = ev
    res["evals"].append({"t": t, "net": net, "pi": pi, "ddd": ddd,
                         "legs": [poc_engine.FX[e] for e in paths[pi] if not isinstance(e, tuple)],
                         "start": poc_engine.CURR[starts[pi]]})
    # exec sur le chemin argmax a 3 latences
    for lat in (1.3, 2.5, 5.0):
        r = eng.exec_seq(t, pi, lat, 0.8, 0.5)
        res["execs"].append({"t": t, "pi": pi, "lat": lat,
                             "res": None if r is None else r["res"],
                             "why": None if r is None else r["why"],
                             "pnl": None if r is None else r["pnl"],
                             "fills": None if r is None else r["fills"],
                             "dur": None if r is None else r["dur"],
                             "parts": None if r is None else r["parts"]})

with open(OUT, "w") as fh:
    json.dump(res, fh, indent=1)
print(json.dumps(res["evals"], indent=1))
print(f"-> {OUT}")
