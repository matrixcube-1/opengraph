#!/usr/bin/env python3
"""Reference pour la parite Strategy Nautilus : replay direct POC des
candidats cand.npz, avec les memes verrous que poc_optuna.replay()
(t_free cooldown + ddd_min) — le cote « attendu » de parity_cmp.py.

Emet le schema parity_ref : {"evals":[{t,net,pi,ddd,legs,start}|{t,none}],
"execs":[{t,pi,lat,res,why,pnl,fills,dur,parts}]}.

Echantillon : pas STEP + tous les chemins a 3 jambes — le meme que
nt_strategy.py (parametre identique des deux cotes).

Usage: venv-og/bin/python replay_ref_srv.py [pas] [out.json]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/opt/arb/poc")
import poc_engine

POC = Path("/opt/arb/poc")
P = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
         lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)
STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else POC / "nt_ref.json"

t0 = time.time()
eng = poc_engine.Engine(verbose=True)
z = np.load(POC / "cand.npz")
cands = z["cands"]
assert np.all(np.diff(cands[:, 0]) >= 0), "cand.npz : timestamps non croissants"

idx = set(range(0, len(cands), STEP))
for k in range(len(cands)):
    legs = [e for e in eng.paths[int(cands[k, 1])] if not isinstance(e, tuple)]
    if len(legs) == 3:
        idx.add(k)
idx = sorted(idx)
print(f"{len(idx):,} candidats echantillonnes (pas={STEP} + 3 jambes)")

evals, execs = [], []
t_free = -1e18
for k in idx:
    t = int(cands[k, 0])
    if t < t_free:                       # verrou cooldown — replay()
        continue
    eng.sync(t)
    ev = eng.eval_at(t, P["fresh_ms"], P["spread_cap"], P["comm_scale"],
                     rows=eng.IDX_FX)
    if ev is None:
        evals.append({"t": t, "none": True})
        continue
    net, pi, ddd = ev
    legs = [poc_engine.FX[e] for e in eng.paths[pi] if not isinstance(e, tuple)]
    evals.append({"t": t, "net": net, "pi": pi, "ddd": ddd,
                  "legs": legs, "start": poc_engine.CURR[eng.p_start[pi]]})
    if net < P["ddd_min"]:               # porte ddd_min — replay()
        continue
    r = eng.exec_seq(t, pi, P["lat_ms"], P["tau_slip_bp"], P["q_min"])
    execs.append({"t": t, "pi": pi, "lat": P["lat_ms"],
                  "res": None if r is None else r["res"],
                  "why": None if r is None else r["why"],
                  "pnl": None if r is None else r["pnl"],
                  "fills": None if r is None else r["fills"],
                  "dur": None if r is None else r["dur"],
                  "parts": None if r is None else r["parts"]})
    if r is not None:
        t_free = t + max(r["dur"], 1) + P["cooldown_s"] * 1000

OUT.write_text(json.dumps({"evals": evals, "execs": execs}, indent=0))
print(f"-> {OUT} : {len(evals)} evals, {len(execs)} execs, "
      f"{time.time()-t0:.0f}s")
