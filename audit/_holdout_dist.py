
import sys
sys.path.insert(0, '/opt/arb/poc')
import numpy as np, optuna
import poc_engine as pe
from poc_optuna import STORAGE, feasible_trials, day_of, HOLDOUT_DAY

def load_cand(path):
    z = np.load(path); out={d:[] for d in pe.DAYS}
    for c in z['cands']:
        d = day_of(int(c[0]))
        if d: out[d].append(tuple(c))
    return out

def pick(name, mn):
    st = optuna.load_study(study_name=name, storage=STORAGE)
    pool=[t for t in feasible_trials(st) if t.params['ddd_min']>=mn]
    return max(pool,key=lambda t:t.value)

def replay_pnl(eng, cands, p):
    t_free=-1e18; pnls=[]; durs=[]; ts=[]
    for t,_p,_n,_d in cands:
        if t<t_free or _n<p['ddd_min']: continue
        eng.sync(int(t))
        ev=eng.eval_at(int(t),p['fresh_ms'],p['spread_cap'],p['comm_scale'],rows=eng.IDX_FX)
        if ev is None or ev[0]<p['ddd_min']: continue
        r=eng.exec_seq(int(t),ev[1],p['lat_ms'],p['tau_slip_bp'],p['q_min'])
        if r is None: continue
        pnls.append(r['pnl']); durs.append(r['dur']); ts.append(t)
        t_free=t+max(r['dur'],1)+p['cooldown_s']*1000
    return np.array(pnls),np.array(durs),np.array(ts)

optuna.logging.set_verbosity(optuna.logging.WARNING)
for name,syms,mixed,candf in [('poc_exec_mixed', pe.FX+[m for c in pe.METAL_SYM.values() for m in c.values()], True,'cand_mixed.npz'),
                             ('poc_exec_fxcur', pe.FX, False,'cand_fxonly_current.npz')]:
    eng=pe.Engine(syms=syms,mixed=mixed,verbose=False)
    cand=load_cand('/opt/arb/poc/'+candf)
    tr=pick(name,2.5)
    pnls,durs,ts=replay_pnl(eng,cand[HOLDOUT_DAY],tr.params)
    q=np.quantile(pnls,[0.1,0.25,0.5,0.75,0.9])
    print('###',name,'trial#',tr.number)
    print(' n=%d sum=%.0f mean=%.2f med=%.2f p10=%.2f p90=%.2f min=%.2f max=%.2f std=%.2f'%(
        len(pnls),pnls.sum(),pnls.mean(),q[2],q[0],q[4],pnls.min(),pnls.max(),pnls.std()))
    print(' durs ms : med=%.0f p90=%.0f max=%.0f'%(np.median(durs),np.quantile(durs,0.9),durs.max()))
    e0=pe.EPOCH[HOLDOUT_DAY]
    for h in range(24):
        m=(ts>=e0+h*3600000)&(ts<e0+(h+1)*3600000)
        if m.any(): print('  %02dh : n=%d pnl=%.0f'%(h,m.sum(),pnls[m].sum()))
