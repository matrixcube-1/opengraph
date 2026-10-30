import sys
sys.path.insert(0, '/opt/arb/poc')
import numpy as np
from poc_engine import Engine, DAYS, EPOCH
from poc_optuna import load_cands

eng = Engine(verbose=False)
by_day, _ = load_cands()

# params du trial 5 (ddd_min=21.86) — reproduire ses acceptations
p = dict(fresh_ms=176.0666, spread_cap=1.7447067, comm_scale=0.8488730,
         ddd_min=21.8576, lat_ms=3.9153, tau_slip_bp=0.6723, q_min=0.7650)

n_pass = 0; shown = 0
for t, pi0, net0, ddd0 in by_day[DAYS[0]][:200000]:
    eng.sync(int(t))
    ev = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"], p["comm_scale"], rows=eng.IDX_FX)
    if ev is not None and ev[0] >= p["ddd_min"]:
        ev2 = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"], p["comm_scale"])
        if ev2 and ev2[0] > ev[0]:
            ev = ev2
    if ev is None or ev[0] < p["ddd_min"]:
        continue
    n_pass += 1
    if shown < 5:
        pi = ev[1]
        path = eng.paths[pi]
        legs = [e_ for e_ in path if isinstance(e_, int)]
        syms = [eng.fe and __import__('poc_engine').FX[e_] for e_ in legs]
        print(f"t={int(t)} stored_net={net0:.2f} eval_net={ev[0]:.2f} pi={pi} "
              f"S={__import__('poc_engine').CURR[eng.p_start[pi]]} legs={syms}")
        r = eng.exec_seq(int(t), pi, p["lat_ms"], p["tau_slip_bp"], p["q_min"])
        print("   exec:", r)
        shown += 1
print("passes sur 200k:", n_pass)
