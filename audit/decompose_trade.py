# -*- coding: utf-8 -*-
"""Decomposition jambe par jambe d'un trade — reconcile eval_at vs exec_seq."""
import sys, numpy as np
sys.path.insert(0, '/opt/arb/poc')
from poc_engine import *
import poc_optuna as po

eng = Engine(verbose=False)
by_day, _ = po.load_cands()
p = dict(fresh_ms=500., spread_cap=1.7, comm_scale=1.0,
         lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)

for t, pi0, net0, ddd0 in sorted(by_day[DAYS[0]], key=lambda c: -c[2])[:200]:
    t = int(t)
    eng.sync(t)
    ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"], p["comm_scale"],
                     rows=eng.IDX_FX)
    if ev is None or ev[0] < 10:
        continue
    r = eng.exec_seq(t, ev[1], p["lat_ms"], p["tau_slip_bp"], p["q_min"])
    if r is None or r["res"] != "done":
        continue

    pi = ev[1]; path = eng.paths[pi]
    legs = [e for e in path if not isinstance(e, tuple)]
    print(f"t={t} pi={pi} eval_net={ev[0]:.2f} -> exec pnl={r['pnl']:.2f} "
          f"fills={r['fills']} dur={r['dur']}ms")
    print(f"  start={CURR[eng.p_start[pi]]}  " +
          " -> ".join(FX[e] for e in legs))

    # --- replication manuelle de la marche exec_seq ---
    eng.sync(t)
    ok = np.ones(len(eng.slist), bool)
    up = eng.usd_price(ok)
    node = eng.p_start[pi]
    qty = NOTIONAL / up[node]; cash = comm = 0.0; tt = t
    print(f"  qty0 = 100000/up[{CURR[node]}]={NOTIONAL/up[node]:,.2f} {CURR[node]}")
    for k, ei in enumerate(legs):
        s = FX[ei]; base_i, quote_i = eng.fe[s]
        d = +1 if node == base_i else -1
        t_f = tt + p["lat_ms"]
        jt0, bs0, as0, _ = eng.quote_at(s, tt)
        jt, bid_f, ask_f, j = eng.quote_at(s, t_f)
        q_send = bs0 if d == +1 else 1.0 / as0
        q_now = bid_f if d == +1 else 1.0 / ask_f
        move = abs(q_now / q_send - 1.0)
        frac = 1.0 if move <= p["tau_slip_bp"] * 1e-4 else 0.6
        cm = COMM / up[base_i]
        cash_in = qty * (1 - frac) * up[node]
        print(f"  j{k} {s} {'BID' if d>0 else 'ASK'} q={q_now:.5f} "
              f"(age_tick={t_f-jt}ms move={move*1e4:.1f}bp frac={frac}) "
              f"qty {qty:,.0f}->{qty*frac*q_now:,.0f} comm=${cm:.2f}"
              + (f" cash_resid=${cash_in:.2f}" if frac < 1 else ""))
        cash += cash_in; qty = qty * frac * q_now; comm += cm
        node = quote_i if node == base_i else base_i
        tt = t_f
    usd_end = qty * up[node]
    pnl_manual = usd_end + cash - NOTIONAL - comm
    print(f"  usd_end={usd_end:.2f} cash={cash:.2f} comm={comm:.2f} "
          f"-> pnl manuel={pnl_manual:.2f} vs exec={r['pnl']:.2f} "
          f"diff={pnl_manual-r['pnl']:.4f}")
    print(f"  MARGE = {pnl_manual/NOTIONAL*1e4:.2f} bp = "
          f"{pnl_manual/NOTIONAL*1e4/1.12:.2f} pip-equiv EURUSD")
    break
else:
    print("aucun trade done dans les 200 premiers candidats")
