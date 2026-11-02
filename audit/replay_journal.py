#!/usr/bin/env python3
"""replay_journal.py — PoC sur donnees reelles cTrader.

Rejoue le journal OgDetect/client Rust (JSONL ticks) dans le MOTEUR RUST
reel (pybind) : ingestion quote par quote (charge le livre complet +
detection par tick), puis exec_seq sequential pour chaque opportunite
(lat, IOC, partiels, abort, unwind, liquidation Convention A).
Repond a la vraie question du PoC : les nets detectes survivent-ils a
l'execution sur le flux venue reel ?

Usage : python replay_journal.py JOURNAL.jsonl [--lat 2.5] [--tau 0.69]

ATTENTION granularite : le journal cBot = quotes echantillonnees au tick
du symbole hote (~1-2/s) — entre deux ticks le livre est forward-fille,
le replay sous-estime les changements intra-seconde (les kills IOC sur
micro-mouvement peuvent etre sous-estimes). Resultats = borne optimiste
cote fills, borne pessimiste cote detection (des opportunites <1s
peuvent etre manquees par le poll).
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import poc_engine as pe                     # noqa: E402
import opengraph as og                      # noqa: E402

# parametres trial #87 (config.toml — garder synchronises)
FRESH, CAP, SCALE, DDD = 909.0, 1.76, 1.10, 2.66
QMIN = 0.5


def iter_ticks(path):
    for line in open(path, encoding="utf-8"):
        if '"tick"' not in line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("ev") != "tick":
            continue
        yield int(ev["e"]), int(ev["ts"]), float(ev["bid"]), float(ev["ask"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("journal")
    ap.add_argument("--lat", type=float, default=2.5, help="latence jambe ms")
    ap.add_argument("--tau", type=float, default=0.69, help="tau_slip bp")
    ap.add_argument("--max-exec", type=int, default=500)
    args = ap.parse_args()

    # Phase A : charge le livre complet + detection par tick (on_tick
    # renvoie l'eval du tick — meme semantique que le scan live).
    eng = og.Engine.live()
    opps, n_ticks, t0, last_t = [], 0, None, 0
    for e, t, bid, ask in iter_ticks(args.journal):
        if t0 is None:
            t0 = t
        r = eng.on_tick(e, t, bid=bid, ask=ask, vol=0.0,
                        fresh_ms=FRESH, spread_cap=CAP, comm_scale=SCALE)
        n_ticks += 1
        last_t = t
        if r is not None and r[1] >= DDD:
            opps.append((t, r[0], r[1], r[2]))
    dur_h = (last_t - t0) / 3_600_000 if t0 else 0
    # Regroupe les detections consecutives du meme chemin en episodes :
    # une fenetre stale est re-detectee a chaque tick tant qu'elle dure
    # (fin d'episode = silence > FRESH sur le meme pi, comme le MC).
    episodes = []
    for t, pi, net, ddd in opps:
        if episodes and episodes[-1][0] == pi and t - episodes[-1][2] <= FRESH:
            ep = episodes[-1]
            ep[2] = t
            if net > ep[3]:
                ep[3], ep[4] = net, t      # pic de l'episode
        else:
            episodes.append([pi, t, t, net, t])   # pi, debut, fin, pic, t_pic
    print(f"{n_ticks:,} ticks sur {dur_h:.2f} h -> {len(opps)} detections, "
          f"{len(episodes)} episodes ({len(episodes)/dur_h if dur_h else 0:.1f}/h)\n")
    # on execute au PIC de chaque episode (nettete max -> conservateur)
    opps = [(ep[4], ep[0], ep[3], None) for ep in episodes]

    # Phase B : execution — la serie complete est chargee, exec_seq
    # lit les quotes a t (envoi) et t+lat (fill) dans la serie stockee.
    stats, pnls = Counter(), []
    nets_det = [o[2] for o in opps]
    for k, (t, pi, net, ddd) in enumerate(opps[:args.max_exec]):
        res, pnl, fills, parts, dur = eng.exec_seq(
            t, pi, lat_ms=args.lat, tau_slip_bp=args.tau, q_min=QMIN,
            vol_guard=False)
        stats[res] += 1
        pnls.append(pnl)
        info = eng.path_info(pi)
        legs = "->".join(pe.FX[e] for e, *_ in info)
        print(f"  [{k+1}] det=+{net:.2f}$ -> {res:20s} pnl={pnl:+8.2f}$ "
              f"fills={fills} dur={dur}ms  {legs}")

    n = len(pnls)
    print(f"\n=== PoC reel ({n} executions, lat={args.lat}ms, tau={args.tau}bp) ===")
    print("resultats:", dict(stats))
    if n:
        pn = sorted(pnls)
        det_mean = sum(nets_det[:n]) / n
        print(f"pnl/trade: mean {sum(pnls)/n:+.2f}$ | p50 {pn[n//2]:+.2f} "
              f"| min {pn[0]:+.2f} | max {pn[-1]:+.2f}")
        if det_mean:
            print(f"detecte moyen {det_mean:+.2f}$ -> realise moyen "
                  f"{sum(pnls)/n:+.2f}$ (survie {100*(sum(pnls)/n)/det_mean:.0f}%)")


if __name__ == "__main__":
    main()
