#!/usr/bin/env python3
"""MC cTrader — campagne aux conditions réelles du cBot OgTrade.

Réutilise le générateur falsifiable de mc_realiste (latent cohérent, edge
uniquement par désynchronisation) mais aux paramètres MESURÉS live :

  lat_ms   = 350 (envoi -> fill observé sur le demo, 10/11)
  fresh_ms = 8000 (pas de heartbeat : p99 gap inter-ticks ~6,1 s mesure)
  comm     = 4,00 /100K/côté (fills réels — comm_per_side Rust par défaut)
  spread_cap/comm_scale/ddd_min : grille

Le moteur embarque désormais le coût aller-retour marge (unwind par
jambe, commit 0a4f877) : le P&L simulé paie les clôtures réelles —
même sémantique que les settles OgTrade.

Usage : python mc_ctrader.py [n_sessions] [dur_min]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import mc_realiste as mc  # noqa: E402


def main():
    n_sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    dur_min = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    ss = np.random.SeedSequence(20261110)
    seeds = [int(s.generate_state(1)[0]) for s in ss.spawn(n_sessions + 4)]
    t0 = time.time()
    out = {"seed_master": 20261110, "n_sessions": n_sessions,
           "dur_min": dur_min, "scenarios": {}}

    def campaign(name, **kw):
        rs = [mc.run_session(sd, dur_min, **kw) for sd in seeds[:n_sessions]]
        s = mc.summarize(rs)
        out["scenarios"][name] = s
        print(f"[{name}] trades={s['trades']} aborts={s['aborts']} "
              f"wr={s['wr']:.1%} pnl/sess={s['sess_pnl_mean']:+.2f}$ "
              f"p05={s['p05']:+.2f} p50={s['p50']:+.2f} "
              f"({time.time()-t0:.0f}s)", flush=True)
        return s

    # contrôle falsification — toujours exigé en premier
    s = mc.summarize([mc.run_session(sd, dur_min, mode="sync")
                      for sd in seeds[:4]])
    out["scenarios"]["sync_control"] = s
    out["falsified"] = bool(s["trades"] and (s["wr"] > 0.20 or s["pnl_mean"] > 0))
    print(f"[sync_control] trades={s['trades']} wr={s['wr']:.1%} "
          f"pnl/sess={s['sess_pnl_mean']:+.2f}$ "
          f"({'FALSIFIE' if out['falsified'] else 'OK'} — {time.time()-t0:.0f}s)",
          flush=True)

    # conditions cBot réelles : lat 350, fresh 8000 — seuil courant vs grille
    base = dict(fresh_ms=8000.0, spread_cap=1.76, comm_scale=1.10,
                ddd_min=0.27, lat_ms=350.0, tau_slip_bp=0.8, q_min=0.5,
                cooldown_s=2.0)
    campaign("ctrader_actuel", params=dict(base))
    for ddd in (0.5, 1.0, 1.5, 2.5, 5.0):
        campaign(f"ddd_{ddd}", params=dict(base, ddd_min=ddd))
    # borne latence : mêmes seuils à lat ECN 5 ms (ce que la colocation
    # changerait) — quantifie la part de la perte due à la distance
    campaign("lat_5ms_ref", params=dict(base, lat_ms=5.0))
    # sensibilité spread : venue ECN à spread/2 (2 $ A/R /100K -> scale 0.5
    # n'est pas le spread — on simule spread halved)
    campaign("spread_x0.5_lat350", params=dict(base), spread_mult=0.5)

    out["elapsed_s"] = time.time() - t0
    dst = Path(__file__).parent.parent / "MC" / "mc_ctrader_results.json"
    dst.write_text(json.dumps(out, indent=1))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
