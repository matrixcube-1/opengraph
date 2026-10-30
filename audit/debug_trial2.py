import sys
sys.path.insert(0, '/opt/arb/poc')
import numpy as np
import poc_engine as pe
from poc_engine import Engine, DAYS
from poc_optuna import load_cands

eng = Engine(verbose=False)
by_day, _ = load_cands()
p = dict(fresh_ms=176.0666, spread_cap=1.7447067, comm_scale=0.8488730,
         ddd_min=21.8576, lat_ms=3.9153, tau_slip_bp=0.6723, q_min=0.7650)

n_pass = n_pos = shown = 0
t_free = -1e18
for t, pi0, net0, ddd0 in by_day[DAYS[0]]:
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
    n_pass += 1
    r = eng.exec_seq(t, ev[1], p["lat_ms"], p["tau_slip_bp"], p["q_min"])
    if r is None:
        continue
    t_free = t + max(r["dur"], 1) + 0
    if r["pnl"] > 0:
        n_pos += 1
        if shown < 6:
            pi = ev[1]
            node = eng.p_start[pi]; seq = [pe.CURR[node]]
            for e_ in eng.paths[pi]:
                if isinstance(e_, tuple):
                    seq.append("B:" + e_[1]); continue
                a, b = eng.fe[pe.FX[e_]]
                node = b if node == a else a; seq.append(pe.CURR[node])
            print(f"t={t} eval={ev[0]:.2f} exec_pnl={r['pnl']:.2f} res={r['res']} "
                  f"path={' -> '.join(seq)} dur={r['dur']}")
            shown += 1
print(f"passes={n_pass} trades_pnl_pos={n_pos}")
