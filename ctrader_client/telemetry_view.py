# Télémétrie live — console temps réel du client cTrader.
# Usage : python -u telemetry_view.py
import json, time, datetime, os, glob, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # journal/ relatif au script

FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP",
      "EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD",
      "GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","CADJPY","CADCHF",
      "CHFJPY","NZDJPY","NZDCAD","NZDCHF"]

def jfile(sub=''):
    fs = sorted(glob.glob('journal/%s20*.jsonl' % sub))
    return fs[-1] if fs else None

def hm(ts_ms=None):
    t = datetime.datetime.fromtimestamp(ts_ms/1000) if ts_ms else datetime.datetime.now()
    return t.strftime('%H:%M:%S')

def pair(e):
    return FX[e] if 0 <= e < len(FX) else 'e%d' % e

def stat(v):
    v = sorted(v)
    return v[len(v)//2] if v else 0

sends, bycoid, seen = {}, {}, set()
opp_best, opp_last = {}, {}
cum_pnl = 0.0
n_fill = n_part = 0
lat_tot, lat_ven, lat_acc, qs = [], [], [], []
pos = 0 if '--all' in sys.argv else None   # défaut : live only (EOF), --all = replay
last_stat = time.time()
tprev = os.path.getsize(jfile('ticks/')) if jfile('ticks/') else 0

print("="*78)
print(" ARB TELEMETRY — %s | pairs | latence E2E | pnl cycle+cum" % hm())
print("="*78)

while True:
    f = jfile()
    if f:
        try:
            if os.path.getsize(f) < pos:
                pos = 0
            with open(f, encoding='utf8', errors='replace') as fh:
                if pos is None:
                    pos = os.path.getsize(f)   # premier passage : sauter l'historique
                fh.seek(pos)
                for l in fh:
                    try:
                        d = json.loads(l)
                    except Exception:
                        continue
                    e = d.get('ev'); ts = d.get('ts', 0)
                    key = (e, ts, json.dumps(d, sort_keys=True)[:80])
                    if key in seen:
                        continue
                    seen.add(key)

                    if e == 'opportunity':
                        # un épisode persistant est journalisé à chaque tick :
                        # n'afficher que la 1re occurence (ou un nouveau best net)
                        k = d['pi']
                        if d['net_usd'] > opp_best.get(k, -1e18) or \
                           (ts - opp_last.get(k, 0)) > 60_000:
                            opp_best[k] = d['net_usd']; opp_last[k] = ts
                            print("%s OPP   pi=%-4d net=%6.2f$ ddd=%5.2f$ legs=%d" % (
                                hm(ts), d['pi'], d['net_usd'], d['ddd_usd'], d['legs']))
                    elif e == 'leg_send':
                        sends[d['coid']] = d
                        print("%s SEND  %-6s %-4s %7.0f @%.5f  (%s)" % (
                            hm(ts), pair(d['edge']), d['side'], d['volume'],
                            d['limit_px'], d['coid'][-6:]))
                    elif e == 'exec_stamp':
                        bycoid.setdefault(d['coid'], []).append(d)
                        s = sends.get(d['coid'])
                        if d['etype'] == 'ACC' and s:
                            dt = d['rx_ms'] - s['ts']; lat_acc.append(dt)
                            print("%s   ACC +%dms (send->accept, horloge locale)" % (hm(ts), dt))
                        elif d['etype'] == 'FILL':
                            acc = [x for x in bycoid[d['coid']] if x['etype'] == 'ACC']
                            if acc:
                                v = d['d_ts'] - acc[0]['o_ts']
                                lat_ven.append(v)
                                tot = d['rx_ms'] - s['ts'] if s else -1
                                if tot >= 0: lat_tot.append(tot)
                                qs.append(d['q_ms'])
                                print("%s   FILL venue A->F %dms | q=%d | send->fill %dms" % (
                                    hm(ts), v, d['q_ms'], tot))
                    elif e == 'leg_fill':
                        n_fill += 1
                        frac = d.get('frac_eff', 1)
                        tag = ' PARTIEL' if frac < 0.999 else ''
                        if frac < 0.999: n_part += 1
                        print("%s   done %-6s qty=%6.0f @%.5f frac=%.2f%s" % (
                            hm(ts), pair(d['edge']), d['qty_base'], d['px_fill'], frac, tag))
                    elif e == 'residual':
                        print("%s RESID %-6s %9.2f à clore" % (hm(ts), pair(d['edge']), d['qty_base']))
                    elif e == 'unwind_send':
                        print("%s UW->  %-6s %-4s %6.0f @%.5f" % (
                            hm(ts), pair(d['edge']), d['side'], d['qty_base'], d['limit_px']))
                    elif e == 'unwind_fill':
                        print("%s UW ok %-6s %6.0f @%.5f" % (
                            hm(ts), pair(d['edge']), d['qty_base'], d['px_fill']))
                    elif e == 'settle':
                        cum_pnl += d['pnl_usd']
                        print("%s SETT  pi=%-4d PnL=%+6.2f$ | CUMULE %+7.2f$ | dur=%dms%s" % (
                            hm(ts), d['pi'], d['pnl_usd'], cum_pnl, d['dur_ms'],
                            ' ABORT' if d['aborted'] else ''))
                    elif e == 'venue_error':
                        print("%s ERR   %s %s -> %s" % (hm(ts), d['code'], d['desc'][:40], d['action']))
                    elif e == 'system':
                        print("%s SYS   %s" % (hm(ts), d.get('msg', '')[:70]))
                    elif e == 'leg_abort':
                        print("%s ABORT pi=%-4d li=%d %-6s %s" % (
                            hm(ts), d['pi'], d['li'], pair(d['edge']), d['reason']))
                pos = fh.tell()
        except Exception:
            pass

    if time.time() - last_stat > 30:
        tf = jfile('ticks/')
        tn = os.path.getsize(tf) if tf else 0
        rate = (tn - tprev) / 30 / 95; tprev = tn
        print("-"*78)
        print("%s STATS | t/s=%.0f fills=%d(partiel=%d) | lat med: acc=%dms venue=%dms total=%dms q=%dms | PnL cum %+7.2f$" % (
            hm(), rate, n_fill, n_part, stat(lat_acc), stat(lat_ven),
            stat(lat_tot), stat(qs), cum_pnl))
        print("-"*78)
        last_stat = time.time()
    time.sleep(2)
