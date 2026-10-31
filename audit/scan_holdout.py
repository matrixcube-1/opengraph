#!/usr/bin/env python3
"""Scan des candidats sur le jour holdout 2026-10-27 — meme pipeline que
poc_optuna.py scan (SCAN_GENEROUS), sortie cand_holdout.npz.

Usage : python3 scan_holdout.py   (serveur, /opt/arb/poc)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import poc_engine

# jour holdout : on re-pointe les globals de chargement
poc_engine.DAYS = ["2026-10-27"]
poc_engine.EPOCH = {
    d: int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000)
    - time.timezone * 1000
    for d in poc_engine.DAYS
}

# bornes identiques au scan in-sample (poc_optuna.SCAN_GENEROUS)
SCAN_GENEROUS = dict(fresh_ms=2000, spread_cap=3.0, thresh=-50.0)

if __name__ == "__main__":
    t0 = time.time()
    eng = poc_engine.Engine()
    cands, eps = eng.scan(
        out=Path("/opt/arb/poc/cand_holdout.npz"),
        move_gate=0.25, eval_every_ms=500, **SCAN_GENEROUS)
    print(f"scan {time.time()-t0:.0f}s -> {len(cands):,} candidats, "
          f"{len(eps):,} episodes", flush=True)
