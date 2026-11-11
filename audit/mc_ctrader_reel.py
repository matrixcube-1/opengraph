#!/usr/bin/env python3
"""MC REEL cTrader — le bot qui tourne, replayé sur ses propres ticks.

Différence avec mc_realiste.py / mc_fix44.py : AUCUN générateur. Le flux
est le journal réel du cBot (C:\\arb_og_trade\\*.jsonl — les quotes
cTrader des 28 paires, incluant la session overnight 10-11/11). Le
détecteur est le moteur Rust de production (modèle panier corrigé),
paramétré EXACTEMENT comme l'instance live :

    NOTIONAL 10K · comm 4.00$/100K/side -> 0.40$/side · FreshMs 8000
    SpreadCap 1.76 · CommScale 1.10 · DddMin 0.4 · MaxRangeBp 2.08
    QMin 0.5 · MinSeqGapMs 2000 · warm-up 100 ticks/symbole
    MaxHoldMs 5000 (borne du verrou temporel post-séquence)

Monte Carlo : exec_seq est déterministe à latence fixée — on tire K
latences par opportunité dans la distribution MESURÉE cTrader
(wire 250-330ms + detect->send ~10ms) : N(280,35) tronquée [180,420].
Chaque tirage = une exécution alternative du MÊME instant de marché réel
(fill sur les ticks réellement publiés après t0). Le scénario `fix_like`
rejoue les mêmes opportunités sous une latence N(3,1.5) pour mesurer ce
que la migration FIX colo achèterait SUR LE MÊME FLUX.

Le lock "une quote = un trade" est conservé : après un fill sur une
arête, celle-ci est consommée jusqu'à sa prochaine cotation (les
concurrents d'arbitrage épuisent le sommet du livre — sinon le même
quote stale serait frappé en boucle).

Métriques : cands = evals dont net >= DddMin passant les verrous ;
trades = exécutions (K par opp) ; pnl/tirage ; gap = net_send - pnl
realisé = coût réel d'exécution (latence + IOC + partiels + unwind).

Usage : python mc_ctrader_reel.py [n_rep] [journal_glob]
"""
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np
import opengraph

sys.path.insert(0, str(Path(__file__).parent))
import poc_engine as pe  # noqa: E402

FX = pe.FX
N = len(FX)

# --- paramètres = instance live cTrader (boot line 08:11 vérifiée) -----
FRESH_MS = 8000.0
SPREAD_CAP = 1.76
COMM_SCALE = 1.10
DDD_MIN = 0.4
COMM_SIDE = 0.40           # 4.00 $/100K/side -> 0.40 $/side à 10K
TAU_SLIP_BP = 2.08         # MaxRangeBp
Q_MIN = 0.5
MIN_SEQ_GAP_MS = 2000
MAX_HOLD_MS = 5000
WARMUP = 100
THRESH = -30.0             # sur-ensemble (même convention que mc_realiste)

# latence detect->fill mesurée cTrader : wire ~250-330ms + traitement
LAT_MU, LAT_SD, LAT_LO, LAT_HI = 280.0, 35.0, 180.0, 420.0
# variante "et si FIX colo" sur le même flux
FIX_MU, FIX_SD, FIX_LO, FIX_HI = 3.0, 1.5, 1.0, 8.0


def load_journal(pattern):
    """Journal cBot -> {edge: (ts,bid,ask)} trié + events (ts,e) uniques."""
    streams = {e: ([], [], []) for e in range(N)}
    for f in sorted(glob.glob(pattern)):
        for line in open(f, encoding="utf-8"):
            if '"ev":"tick"' not in line:
                continue
            r = json.loads(line)
            e = int(r["e"])
            if 0 <= e < N:
                streams[e][0].append(int(r["ts"]))
                streams[e][1].append(float(r["bid"]))
                streams[e][2].append(float(r["ask"]))
    events = []
    out = {}
    for e, (ts, bid, ask) in streams.items():
        t = np.asarray(ts, dtype=np.int64)
        o = np.argsort(t, kind="stable")
        out[e] = (t[o], np.asarray(bid)[o], np.asarray(ask)[o])
        events.extend(zip(t[o].tolist(), [e] * len(t)))
    events.sort()
    return out, events


def run_replay(ticks, events, n_rep, lat_dist, seed=1):
    """Replay detection sur flux réel + K exec_seq par opportunité."""
    rng = np.random.default_rng(seed)
    eng = opengraph.Engine.live()
    eng.set_comm_per_side(COMM_SIDE)

    # phase A : ingestion complète (popule les séries internes)
    for e in range(N):
        ts, bid, ask = ticks[e]
        for i in range(len(ts)):
            eng.on_tick(e, int(ts[i]), float(bid[i]), float(ask[i]), 1.0,
                        FRESH_MS, SPREAD_CAP, COMM_SCALE)

    tick_ts = {e: ticks[e][0] for e in range(N)}
    n_ticks = np.zeros(N, dtype=np.int64)
    lock_until = np.full(N, -1e18)
    opps = []               # (t, pi, net, ddd)
    cands = pos = evals = consumed = 0
    t_free = -1e18
    last_t = -1
    for t, e in events:
        n_ticks[e] += 1
        if t == last_t:
            continue
        last_t = t
        evals += 1
        ev = eng.eval_at(t, FRESH_MS, SPREAD_CAP, COMM_SCALE)
        if ev is None:
            continue
        pi, net, ddd = ev
        if net > 0.0:
            pos += 1
        if net <= THRESH:
            continue
        cands += 1
        if (n_ticks < WARMUP).any() or t < t_free or net < DDD_MIN:
            continue
        legs = [FX.index(s) for s in eng.legs(pi)]
        # verrou sur le PANIER COMPLET : jambes + arête USD d'entrée (s0!=USD)
        # + arête USD de sortie (end!=USD) — les arêtes USD sont consommées
        # au même titre que les jambes (même quote = un seul trade)
        basket_edges = set(legs)
        s0 = pe.CURR.index(eng.start(pi))
        end = eng.end_node(pi)
        for c in (s0, end):
            if c != 0:  # USD = node 0
                for nm in (pe.CURR[c] + "USD", "USD" + pe.CURR[c]):
                    if nm in pe.FX:
                        basket_edges.add(pe.FX.index(nm))
                        break
        if any(lock_until[x] > t for x in basket_edges):
            consumed += 1
            continue
        lats = np.clip(rng.normal(*lat_dist[:2], n_rep), *lat_dist[2:])
        for lat in lats:
            r = eng.exec_seq(t, pi, float(lat), TAU_SLIP_BP, Q_MIN, False)
            if r is None:
                continue
            res, pnl, filled, parts, dur = r
            opps.append(dict(t=t, pi=pi, net=net, ddd=ddd, lat=float(lat),
                             res=res, pnl=pnl, filled=filled, parts=parts,
                             dur=dur))
        for x in basket_edges:
            j = int(np.searchsorted(tick_ts[x], t, "right"))
            lock_until[x] = tick_ts[x][j] if j < len(tick_ts[x]) else 1e18
        t_free = t + MIN_SEQ_GAP_MS + MAX_HOLD_MS
    return dict(evals=evals, cands=cands, pos=pos, consumed=consumed,
                opps=opps)


def summarize(run, span_h):
    tr = run["opps"]
    pnls = np.array([t["pnl"] for t in tr])
    nets = np.array([t["net"] for t in tr])
    n_t0 = len({t["t"] for t in tr})
    aborts = int(sum(t["res"].startswith("abort") for t in tr))
    res_ct = {}
    for t in tr:
        res_ct[t["res"]] = res_ct.get(t["res"], 0) + 1
    return dict(
        evals=run["evals"], cands=run["cands"], pos_evals=run["pos"],
        consumed=run["consumed"], opps_sent=n_t0, execs=len(tr),
        abort_rate=float(aborts / max(1, len(tr))),
        res_counts=res_ct,
        wr=float((pnls > 0).mean()) if len(tr) else 0.0,
        pnl_mean=float(pnls.mean()) if len(tr) else 0.0,
        p05=float(np.percentile(pnls, 5)) if len(tr) else 0.0,
        p50=float(np.percentile(pnls, 50)) if len(tr) else 0.0,
        p95=float(np.percentile(pnls, 95)) if len(tr) else 0.0,
        pnl_per_hour=float(pnls.sum() / span_h) if span_h > 0 else 0.0,
        gap_mean=float((nets - pnls).mean()) if len(tr) else 0.0,
        opp_per_hour=float(n_t0 / span_h) if span_h > 0 else 0.0,
    )


def main():
    n_rep = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    pattern = sys.argv[2] if len(sys.argv) > 2 else r"C:\arb_og_trade\*.jsonl"
    t_start = time.time()
    ticks, events = load_journal(pattern)
    t_min = min(t[0][0] for t in ticks.values() if len(t[0]))
    t_max = max(t[0][-1] for t in ticks.values() if len(t[0]))
    span_h = (t_max - t_min) / 3_600_000.0
    n_ticks = sum(len(t[0]) for t in ticks.values())
    print(f"[data] {n_ticks} ticks réels, span={span_h:.1f}h, "
          f"events={len(events)}", flush=True)

    out = {"source": pattern, "n_ticks": n_ticks, "span_h": span_h,
           "n_rep": n_rep,
           "params": dict(fresh_ms=FRESH_MS, spread_cap=SPREAD_CAP,
                          comm_scale=COMM_SCALE, ddd_min=DDD_MIN,
                          comm_side=COMM_SIDE, tau_slip_bp=TAU_SLIP_BP,
                          q_min=Q_MIN, min_seq_gap_ms=MIN_SEQ_GAP_MS,
                          max_hold_ms=MAX_HOLD_MS, warmup=WARMUP),
           "scenarios": {}}

    # scénario 1 : conditions réelles cTrader (latence mesurée)
    run = run_replay(ticks, events, n_rep,
                     (LAT_MU, LAT_SD, LAT_LO, LAT_HI), seed=11)
    s = summarize(run, span_h)
    out["scenarios"]["ctrader_lat280"] = dict(summary=s, opps=run["opps"])
    print(f"[ctrader_lat280] opps={s['opps_sent']} execs={s['execs']} "
          f"abort={s['abort_rate']:.0%} wr={s['wr']:.1%} "
          f"pnl/exec={s['pnl_mean']:+.2f}$ p50={s['p50']:+.2f} "
          f"p05={s['p05']:+.2f} gap={s['gap_mean']:+.2f} "
          f"opp/h={s['opp_per_hour']:.1f} ({time.time()-t_start:.0f}s)",
          flush=True)

    # scénario 2 : même flux, latence FIX colo — ce que la migration achète
    run2 = run_replay(ticks, events, n_rep,
                      (FIX_MU, FIX_SD, FIX_LO, FIX_HI), seed=11)
    s2 = summarize(run2, span_h)
    out["scenarios"]["fixlike_lat3"] = dict(summary=s2, opps=run2["opps"])
    print(f"[fixlike_lat3]  opps={s2['opps_sent']} execs={s2['execs']} "
          f"abort={s2['abort_rate']:.0%} wr={s2['wr']:.1%} "
          f"pnl/exec={s2['pnl_mean']:+.2f}$ p50={s2['p50']:+.2f} "
          f"p05={s2['p05']:+.2f} gap={s2['gap_mean']:+.2f} "
          f"opp/h={s2['opp_per_hour']:.1f} ({time.time()-t_start:.0f}s)",
          flush=True)

    out["elapsed_s"] = time.time() - t_start
    dst = Path(__file__).parent.parent / "MC" / "mc_ctrader_reel_results.json"
    dst.write_text(json.dumps(out, indent=1))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
