import sys, collections
sys.path.insert(0, '/opt/arb/poc')
import numpy as np
import poc_engine as pe
from poc_engine import Engine, DAYS
from poc_optuna import load_cands

eng = Engine(verbose=False)
by_day, _ = load_cands()
p = dict(fresh_ms=510.3565, spread_cap=2.4113384, comm_scale=0.9333560,
         ddd_min=0.9585, lat_ms=3.1332, tau_slip_bp=1.9507, q_min=0.3898,
         cooldown_s=3.0705)

res_count = collections.Counter()
pos = []
t_free = -1e18
n_tr = 0
for day in DAYS[:2]:
    for t, pi0, net0, ddd0 in by_day[day]:
        t = int(t)
        if t < t_free:
            continue
        eng.sync(t)
        ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"], p["comm_scale"], rows=eng.IDX_FX)
        if ev is not None and ev[0] >= p["ddd_min"]:
            ev2 = eng.eval_at(t, p["fresh_ms"], p["spread_cap"], p["comm_scale"])
            if ev2 and ev2[0] > ev[0]:
                ev = ev2
        if ev is None or ev[0] < p["ddd_min"]:
            continue
        r = eng.exec_seq(t, ev[1], p["lat_ms"], p["tau_slip_bp"], p["q_min"])
        if r is None:
            continue
        n_tr += 1
        res_count[(r["res"], r.get("why", ""))] += 1
        t_free = t + max(r["dur"], 1) + p["cooldown_s"] * 1000
        if r["pnl"] > 50 and len(pos) < 8:
            pos.append((t, ev[0], ev[1], r))
print("trades:", n_tr, dict(res_count))
for t, net, pi, r in pos:
    node = eng.p_start[pi]; seq = [pe.CURR[node]]
    for e_ in eng.paths[pi]:
        if isinstance(e_, tuple):
            seq.append("B:" + e_[1]); continue
        a, b = eng.fe[pe.FX[e_]]
        node = b if node == a else a; seq.append(pe.CURR[node])
    print(f"t={t} eval={net:.2f} pnl={r['pnl']:.2f} {' -> '.join(seq)} dur={r['dur']} why={r.get('why')}")
