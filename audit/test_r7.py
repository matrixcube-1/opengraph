# -*- coding: utf-8 -*-
"""Test R7 — pas de quote future, pas d'etat fige au rembobinage."""
import sys, numpy as np, datetime as dt
sys.path.insert(0, '/opt/arb/poc')
from poc_engine import *


def day_ms(d, hms='00:00:00'):
    d0 = dt.datetime.strptime(d + ' ' + hms, '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc)
    return int(d0.timestamp() * 1000)


eng = Engine(verbose=False)
paths, starts = eng.paths, eng.p_start

# --- T1: sync avant le 1er tick d'AUDCHF -> aucune quote exposee
t0 = day_ms(DAYS[0])
eng.sync(t0)
i_achf = eng.sidx['AUDCHF']
ts_achf = eng.data['AUDCHF'][0]
assert np.isnan(eng.bid[i_achf]) or eng.last_t[i_achf] <= t0, 'quote future exposee!'
print(f'T1 sync avant 1er tick AUDCHF (1er tick reel a +{(ts_achf[0]-t0)/3.6e6:.1f}h): bid={eng.bid[i_achf]} last_t={eng.last_t[i_achf]:.0f} -> OK')

# --- T2: un chemin AUDCHF ne peut pas etre selectionne a t0
r = eng.eval_at(t0, 5000, 10., 1.0)
if r is not None:
    used = set(a + b for a, b in paths[r[1]])
    assert 'AUDCHF' not in used, f'chemin AUDCHF evalue sans donnee: {paths[r[1]]}'
print('T2 aucun chemin AUDCHF selectionne a t0 -> OK')

# --- T3: fraicheur bilaterale — quote marquee dans le futur rejetee
eng.sync(t0)
eng.last_t[eng.sidx['EURUSD']] = t0 + 500.0            # injection: quote "du futur"
r3 = eng.eval_at(t0, 5000, 10., 1.0)
if r3 is not None:
    assert 'EURUSD' not in set(a + b for a, b in paths[r3[1]]), 'quote future acceptee!'
print('T3 quote marquee +500ms dans le futur rejetee -> OK')

# --- T4: REMBOBINAGE — sync(jour23) puis sync(jour22): pas de quote figee
t22 = day_ms(DAYS[0], '12:00:00'); t23 = day_ms(DAYS[1], '12:00:00')
eng.sync(t23); lt_fwd = eng.last_t.copy()
eng.sync(t22)
assert np.all(eng.last_t <= t22), 'curseur fige apres rembobinage!'
assert lt_fwd[eng.sidx['EURUSD']] - eng.last_t[eng.sidx['EURUSD']] > 3.6e6, 'last_t EURUSD non rembobine'
n_ok = int((eng.last_t > 0).sum())
print(f'T4 rembobinage j23->j22: {n_ok} symboles re-exposes a des ticks <=t22 -> OK')

# --- T5: eval normal jour 22 midi
r5 = eng.eval_at(t22, 5000, 10., 1.0)
print(f'T5 eval j22 midi: {r5}')

# --- T6: replay j22 | j23 | j22 sur le MEME eng -> j22 identique les 2 fois
import poc_optuna as po
by_day, _ = po.load_cands()
p = dict(fresh_ms=510.3565, spread_cap=2.4113, comm_scale=0.9334,
         ddd_min=0.9585, lat_ms=3.1332, tau_slip_bp=1.9507, q_min=0.3898,
         cooldown_s=3.0705)
r1 = po.replay(eng, by_day[DAYS[0]], p)
po.replay(eng, by_day[DAYS[1]], p)
r2 = po.replay(eng, by_day[DAYS[0]], p)
print(f'T6 j22 passe1: {r1}'); print(f'T6 j22 passe2: {r2}')
assert r1 == r2, 'FUITED ETAT inter-replays: j22 differemment rejoue!'
print('T6 rejouabilite inter-trials OK -> plus de fuite d etat')
print('TOUS LES TESTS R7 PASSENT')
