#!/usr/bin/env python3
"""Parite cand.npz : POC Python vs moteur Rust (binding opengraph) — serveur.

Pour chaque candidat echantillonne :
  eval_at(t) sur les deux moteurs  -> net/ddd a rel_tol 1e-9, legs+start identiques
  exec_seq(t, pi, ...)             -> res/pnl/fills/parts/dur identiques (pnl a rel_tol)

Mode parite stricte : vol_guard=False, latence et fills deterministes.
Echantillon : tous les candidats a pas N + tous les chemins a 3 jambes.

Usage: venv-og/bin/python replay_parity_srv.py [pas_echantillon]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, "/opt/arb/poc")
import poc_engine
import opengraph

POC = Path("/opt/arb/poc")
REL_TOL = 1e-9
P = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0,   # DOC_DEFAULTS
         lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5)
STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 20

t0 = time.time()
print("chargement POC Engine...", flush=True)
eng = poc_engine.Engine(verbose=True)

DAYS = poc_engine.DAYS
print(f"DAYS={DAYS} | chargement opengraph Engine...")
og = opengraph.Engine("/opt/arb/data", DAYS)
assert og.n_paths() == 2072

z = np.load(POC / "cand.npz")
cands = z["cands"]
ts = cands[:, 0]
assert np.all(np.diff(ts) >= 0), "cand.npz : timestamps non croissants"
print(f"{len(cands):,} candidats | echantillon pas={STEP}")

# mapping pi POC -> (legs, start) ; les pi IDX_FX n'ont que des jambes FX
def legs_start(pi):
    legs = [e for e in eng.paths[pi] if not isinstance(e, tuple)]
    return [poc_engine.FX[e] for e in legs], poc_engine.CURR[eng.p_start[pi]]

# echantillon : pas STEP + tous les chemins a 3 jambes FX
idx = set(range(0, len(cands), STEP))
for k in range(len(cands)):
    pi = int(cands[k, 1])
    legs = [e for e in eng.paths[pi] if not isinstance(e, tuple)]
    if len(legs) == 3:
        idx.add(k)
idx = sorted(idx)
print(f"{len(idx):,} candidats dans l'echantillon (dont 3 jambes)")


def close(a, b):
    if a == b:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-12) <= REL_TOL


n = n_eval_ok = n_exec = n_exec_ok = 0
mismatches = []
max_rel_pnl = 0.0
for k in idx:
    t, pi0, net0, ddd0 = cands[k]
    t = int(t)
    n += 1
    # --- eval parity ---
    eng.sync(t)
    ev_py = eng.eval_at(t, P["fresh_ms"], P["spread_cap"], P["comm_scale"],
                        rows=eng.IDX_FX)
    ev_rs = og.eval_at(t, P["fresh_ms"], P["spread_cap"], P["comm_scale"])
    if (ev_py is None) != (ev_rs is None):
        mismatches.append(dict(k=k, t=t, kind="eval_none",
                               py=None if ev_py is None else "some",
                               rs=None if ev_rs is None else "some"))
        continue
    if ev_py is None:
        n_eval_ok += 1
        continue
    net_py, pi_py, ddd_py = ev_py
    pi_rs, net_rs, ddd_rs = ev_rs
    legs_py, start_py = legs_start(pi_py)
    legs_rs, start_rs = og.legs(pi_rs), og.start(pi_rs)
    if not (close(net_py, net_rs) and close(ddd_py, ddd_rs)
            and legs_py == legs_rs and start_py == start_rs):
        mismatches.append(dict(k=k, t=t, kind="eval",
                               py=[net_py, legs_py, start_py],
                               rs=[net_rs, legs_rs, start_rs]))
        continue
    n_eval_ok += 1
    # --- exec parity : le chemin argmax doit etre le meme dans les 2 mondes
    pi_map = og.path_index(legs_py, start_py)
    assert pi_map == pi_rs, f"path_index {pi_map} != argmax {pi_rs}"
    r_py = eng.exec_seq(t, pi_py, P["lat_ms"], P["tau_slip_bp"], P["q_min"])
    r_rs = og.exec_seq(t, pi_rs, P["lat_ms"], P["tau_slip_bp"],
                       P["q_min"], False)
    n_exec += 1
    if (r_py is None) != (r_rs is None):
        mismatches.append(dict(k=k, t=t, kind="exec_none"))
        continue
    if r_py is None:
        n_exec_ok += 1
        continue
    res_py = r_py["res"] + ((":" + r_py["why"]) if r_py["res"] == "abort" else "")
    res_rs, pnl_rs, fills_rs, parts_rs, dur_rs = r_rs
    rel = abs(r_py["pnl"] - pnl_rs) / max(abs(r_py["pnl"]), abs(pnl_rs), 1e-12)
    max_rel_pnl = max(max_rel_pnl, rel)
    if not (res_py == res_rs and close(r_py["pnl"], pnl_rs)
            and r_py["fills"] == fills_rs and r_py["parts"] == parts_rs
            and close(r_py["dur"], dur_rs)):
        mismatches.append(dict(k=k, t=t, kind="exec",
                               py=[res_py, r_py["pnl"], r_py["fills"], r_py["parts"], r_py["dur"]],
                               rs=[res_rs, pnl_rs, fills_rs, parts_rs, dur_rs]))
        continue
    n_exec_ok += 1

res = dict(sampled=n, eval_ok=n_eval_ok, exec_ok=n_exec_ok,
           mismatches=len(mismatches), max_rel_pnl=max_rel_pnl,
           rel_tol=REL_TOL, params=P, elapsed_s=round(time.time() - t0, 1))
(POC / "parity_cand.json").write_text(json.dumps(
    {"summary": res, "mismatches": mismatches[:200]}, indent=1))
print(json.dumps(res, indent=1))
