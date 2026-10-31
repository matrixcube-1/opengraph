#!/usr/bin/env python3
"""Balayage du seuil de declenchement sur le flux d'evals du modele REALISTE.

Re-serialise les enregistrements d'insample_legs.json (NT venue, lat 5 ms,
queue, kq=1.0, step=20) sous d'autres (ddd_min, cooldown_s) — verrous
t_free/in_flight identiques a CandRealism.

Biais connu (documente, borne inferieure) : les candidats sautes par t_free
sous l'ancien seuil ne figurent PAS dans le flux -> un seuil plus haut qui
debloquerait du temps de trading ne peut pas etre vu. Le PnL des seuils
> 0 est donc conservateur (il manque des execs nouvellement debloquees).

Variante adaptative : ddd_min_t = clip(hi - k*T_t, lo, hi), T_t = tension
multi-sauts du flux regime (regime_stream.npz, grille 1 s) — teste le seuil
de regime demande.

Jours : 22/23/26 oct. 2026 UNIQUEMENT (in-sample). Le 27 n'est jamais lu.
"""
import json
import sys
from calendar import timegm
from pathlib import Path
from time import strptime

import numpy as np

POC = Path("/opt/arb/poc")
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]
EPOCH = {d: timegm(strptime(d, "%Y-%m-%d")) * 1000 for d in DAYS}


def load_evals():
    """Flux chronologique (t, net, exec|None) par jour — memes verrous que
    le run officiel : un enregistrement existe ssi le candidat a franchi
    t_free/in_flight sous ddd_min=0."""
    d = json.load(open(POC / "insample_legs.json"))
    # eval et exec sont des elements SEPARES du flux ; exec.t = t0 de l'eval
    execs = {e["exec"]["t"]: e["exec"] for e in d["evals"] if "exec" in e}
    out = {day: [] for day in DAYS}
    for rec in d["evals"]:
        if "none" in rec or "net" not in rec:
            continue
        t = rec["t"]
        day = next((dd for dd, e0 in EPOCH.items() if e0 <= t < e0 + 86_400_000),
                   None)
        if day is None:
            continue
        out[day].append((t, rec["net"], execs.get(t)))
    for day in DAYS:
        out[day].sort(key=lambda x: x[0])
    return out


def load_regime():
    z = np.load(POC / "regime_stream.npz", allow_pickle=True)
    return {d: z[d].item() for d in DAYS}


def resim(recs, ddd_min, cooldown_s, epoch=0, T_ts=None, T_val=None,
          adapt=None):
    """Re-serialise un jour sous (ddd_min | adaptatif) + cooldown.

    adapt = (lo, hi, k) -> ddd_min_t = clip(hi - k*T_t, lo, hi).
    T_ts est en offset ms dans le jour ; t est epoch absolu -> t-epoch."""
    pnl = 0.0; n = 0; wins = 0
    t_free = -1e18
    pnls = []
    iT = 0
    for t, net, ex in recs:
        if t < t_free:
            continue
        if adapt is not None and T_ts is not None:
            lo, hi, k = adapt
            toff = t - epoch
            # dernier T connu <= t (flux 1 s, ts = offset ms dans le jour)
            while iT + 1 < len(T_ts) and T_ts[iT + 1] <= toff:
                iT += 1
            thr = min(hi, max(lo, hi - k * T_val[iT]))
        else:
            thr = ddd_min
        if net < thr or ex is None:
            continue
        pnl += ex["pnl"]
        pnls.append(ex["pnl"])
        n += 1
        if ex["pnl"] > 0:
            wins += 1
        t_free = t + max(ex["dur"], 1) + cooldown_s * 1000
    return dict(pnl=pnl, n=n, wr=wins / n if n else 0.0, pnls=pnls)


def score_rows(results):
    """Resultats par config -> metriques : total, pire jour, nb jours < 0."""
    rows = []
    for cfg, by_day in results.items():
        tot = sum(v["pnl"] for v in by_day.values())
        worst = min(v["pnl"] for v in by_day.values())
        rows.append(dict(cfg=cfg, total=round(tot, 2),
                         worst_day=round(worst, 2),
                         n_neg=sum(1 for v in by_day.values() if v["pnl"] < 0),
                         n_exec=sum(v["n"] for v in by_day.values()),
                         per_day={d: round(v["pnl"], 2) for d, v in by_day.items()}))
    rows.sort(key=lambda r: (-(r["worst_day"] >= 0), -r["worst_day"], -r["total"]))
    return rows


def main():
    t0 = __import__("time").time()
    evals = load_evals()
    regime = load_regime()
    print({d: len(v) for d, v in evals.items()}, "evals charges", flush=True)

    # --- grille statique ddd_min x cooldown ---
    grid_ddd = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    grid_cd = [0.0, 10.0, 30.0, 60.0]
    res = {}
    for ddd in grid_ddd:
        for cd in grid_cd:
            key = f"ddd={ddd}|cd={cd}"
            res[key] = {d: resim(evals[d], ddd, cd) for d in DAYS}
    # --- adaptatif : ddd_min_t = clip(hi - k*T, lo, hi) ---
    for lo, hi, k in [(0.5, 6.0, 15.0), (0.5, 8.0, 25.0), (1.0, 8.0, 30.0),
                      (0.0, 4.0, 10.0), (0.5, 10.0, 30.0), (1.0, 6.0, 20.0)]:
        key = f"adapt lo={lo} hi={hi} k={k}"
        res[key] = {}
        for d in DAYS:
            T = regime[d]
            res[key][d] = resim(evals[d], 0.0, 0.0, epoch=EPOCH[d],
                                T_ts=T["ts"], T_val=T["T_raw"], adapt=(lo, hi, k))

    rows = score_rows(res)
    out = dict(elapsed=round(__import__("time").time() - t0, 1), rows=rows)
    (POC / "thresh_grid.json").write_text(json.dumps(out, indent=1))
    for r in rows:
        tag = "OK " if r["n_neg"] == 0 else "   "
        print(f"{tag}{r['cfg']:>28}  total={r['total']:>10}  "
              f"pire_jour={r['worst_day']:>9}  n_neg={r['n_neg']}  "
              f"exec={r['n_exec']:>6}  {r['per_day']}", flush=True)


if __name__ == "__main__":
    main()
