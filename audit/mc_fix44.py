#!/usr/bin/env python3
"""MC FIX 4.4 — conditions du nouveau broker ECN negocie.

Meme generateur falsifiable que mc_realiste (latent coherent, edge par
desynchronisation seule), meme moteur Rust (eval_at/exec_seq — modele
PANIER post-11/11 : USD->s0 + jambes + end->USD, chaque ordre =
position reelle) — mais aux conditions du venue FIX 4.4 :

  comm    = 2 $ A/R/100K -> 1,00/100K/cote -> 0,10 $/position a 10K
            (set_comm_per_side(0.10) — comm_per_side est en $/position,
            pas /100K : comm_usd()=0.40 IC mesure)
  latence = colo annonce ~1 ms — balayage 1/2.5/5/10 ms par ordre
            (FIX session + matching + reseau ; pessimiste vs annonce)
  spread  = balayage x0.5/x0.7/x1.0 de la table IC Raw (meme LPs,
            institutionnel = un peu plus serre a supposer, pas garanti)
  densite ticks : x1.5 LAMBDA en variante (feed ECN plus dense que le
            retail cTrader — plus de fenetres ET plus de concurrence)
  fresh   = 5000 ms (borne ; sur feed dense toutes les quotes sont
            jeunes — le parametre est inerte)

Controle de falsification obligatoire : sync_control doit produire
~0 gagnant, sinon le MC est rejete.

Usage : python mc_fix44.py [n_sessions] [dur_min]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import mc_realiste as mc  # noqa: E402

COMM_SIDE_FIX = 0.10          # 1,00 $/100K/cote x 10K/100K = 0,10 $


def run_fix(seed, dur_min, comm_side=COMM_SIDE_FIX, tick_mult=1.0, **kw):
    """run_session avec commission venue FIX + densite de ticks."""
    lam_backup = mc.LAMBDA.copy()
    if tick_mult != 1.0:
        mc.LAMBDA = mc.LAMBDA * tick_mult
    try:
        import opengraph
        p = dict(mc.P, **(kw.get("params") or {}))
        # injection commission : on bypasse run_session pour pouvoir
        # set_comm_per_side — replique sa logique avec le set en plus.
        ticks, _ = mc.gen_session(seed, dur_min, kw.get("mode", "async"),
                                  kw.get("spread_mult", 1.0))
        eng = opengraph.Engine.live()
        eng.set_comm_per_side(comm_side)
        events = []
        for e in range(mc.N):
            ts, bid, ask, vol = ticks[e]
            for i in range(len(ts)):
                eng.on_tick(e, int(ts[i]), float(bid[i]), float(ask[i]),
                            float(vol[i]), p["fresh_ms"], p["spread_cap"],
                            p["comm_scale"])
                events.append((int(ts[i]), e))
        events.sort()
        tick_ts = {e: ticks[e][0] for e in range(mc.N)}
        lock_until = np.full(mc.N, -1e18)
        cands = n_pos = evals = consumed = 0
        trades = []
        t_free = -1e18
        last_t = -1
        for t, _e in events:
            if t == last_t:
                continue
            last_t = t
            evals += 1
            ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"],
                             p["comm_scale"])
            if ev is None:
                continue
            pi, net, ddd = ev
            if net > 0.0:
                n_pos += 1
            if net <= mc.THRESH:
                continue
            cands += 1
            if t < t_free or net < p["ddd_min"]:
                continue
            legs = [mc.FX.index(s) for s in eng.legs(pi)]
            if any(lock_until[e] > t for e in legs):
                consumed += 1
                continue
            r = eng.exec_seq(t, pi, p["lat_ms"], p["tau_slip_bp"],
                             p["q_min"], False)
            if r is None:
                continue
            res, pnl, filled, parts, dur = r
            trades.append(dict(t=t, pi=pi, res=res, pnl=pnl, filled=filled,
                               parts=parts, dur=dur, net_send=net, ddd=ddd))
            for e in legs:
                j = int(np.searchsorted(tick_ts[e], t, "right"))
                lock_until[e] = tick_ts[e][j] if j < len(tick_ts[e]) else 1e18
            t_free = t + max(dur, 1.0) + p["cooldown_s"] * 1000.0
        pnls = np.array([tr["pnl"] for tr in trades])
        return dict(
            seed=seed, mode=kw.get("mode", "async"), events=len(events),
            evals=evals, cands=cands, pos=n_pos, consumed=consumed,
            trades=len(trades),
            aborts=int(sum(tr["res"].startswith("abort") for tr in trades)),
            abort_reasons={r: sum(tr["res"] == r for tr in trades)
                           for r in {tr["res"] for tr in trades}
                           if r != "done"},
            wins=int((pnls > 0).sum()) if len(pnls) else 0,
            pnl=float(pnls.sum()) if len(pnls) else 0.0,
            pnl_mean=float(pnls.mean()) if len(pnls) else 0.0,
            dur_mean=float(np.mean([tr["dur"] for tr in trades]))
            if trades else 0.0,
            trades_detail=trades,
        )
    finally:
        mc.LAMBDA = lam_backup


def main():
    n_sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    dur_min = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    ss = np.random.SeedSequence(20261111)
    seeds = [int(s.generate_state(1)[0]) for s in ss.spawn(n_sessions + 4)]
    t0 = time.time()
    out = {"seed_master": 20261111, "n_sessions": n_sessions,
           "dur_min": dur_min, "comm_side": COMM_SIDE_FIX, "scenarios": {}}

    def campaign(name, seeds, **kw):
        rs = [run_fix(sd, dur_min, **kw) for sd in seeds]
        s = mc.summarize(rs)
        out["scenarios"][name] = s
        print(f"[{name}] trades={s['trades']} aborts={s['aborts']} "
              f"wr={s['wr']:.1%} pnl/sess={s['sess_pnl_mean']:+.2f}$ "
              f"p05={s['p05']:+.2f} p50={s['p50']:+.2f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        return s

    # 1) controle falsification — horloge commune -> ~0 gagnant attendu
    s = mc.summarize([run_fix(sd, dur_min, mode="sync")
                      for sd in seeds[:4]])
    out["scenarios"]["sync_control"] = s
    out["falsified"] = bool(s["trades"] and
                            (s["wr"] > 0.20 or s["pnl_mean"] > 0))
    print(f"[sync_control] trades={s['trades']} wr={s['wr']:.1%} "
          f"pnl/sess={s['sess_pnl_mean']:+.2f}$ "
          f"({'FALSIFIE' if out['falsified'] else 'OK'} — {time.time()-t0:.0f}s)",
          flush=True)

    base = dict(fresh_ms=5000.0, spread_cap=1.76, comm_scale=1.0,
                ddd_min=0.20, lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5,
                cooldown_s=2.0)

    # 2) reference FIX : lat 2.5 ms, spread IC Raw x1.0, comm 0.10
    campaign("fix_ref_lat2.5", seeds[:n_sessions], params=dict(base))
    # 3) grille latence (l'avantage colo annonce ~1 ms vs pessimiste 10 ms)
    for lat in (1.0, 5.0, 10.0):
        campaign(f"fix_lat{lat}", seeds[:n_sessions],
                 params=dict(base, lat_ms=lat))
    # 4) grille spread : ECN institutionnel serre vs IC Raw
    for sm in (0.5, 0.7):
        campaign(f"fix_spread_x{sm}", seeds[:n_sessions],
                 params=dict(base), spread_mult=sm)
    # 5) grille seuil : sous comm 0.10 les edges marginaux deviennent viables
    for ddd in (0.0, 0.1, 0.4, 0.8):
        campaign(f"fix_ddd_{ddd}", seeds[:n_sessions],
                 params=dict(base, ddd_min=ddd))
    # 6) feed ECN plus dense (x1.5 ticks) — fenetres ET concurrence
    campaign("fix_dense_x1.5", seeds[:n_sessions],
             params=dict(base), tick_mult=1.5)

    out["elapsed_s"] = time.time() - t0
    dst = Path(__file__).parent.parent / "MC" / "mc_fix44_results.json"
    dst.write_text(json.dumps(out, indent=1))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
