import sys
sys.path.insert(0, '/opt/arb/poc')
import numpy as np
from poc_engine import Engine, DAYS
import poc_optuna as po

eng = Engine(verbose=False)
by_day, _ = po.load_cands()
p = dict(fresh_ms=510.3565, spread_cap=2.4113384, comm_scale=0.9333560,
         ddd_min=0.9585, lat_ms=3.1332, tau_slip_bp=1.9507, q_min=0.3898,
         cooldown_s=3.0705)
tot = 0
for d in DAYS[:2]:
    r = po.replay(eng, by_day[d], p)
    print(d, r)
    tot += r["pnl"]
print("total:", tot)
