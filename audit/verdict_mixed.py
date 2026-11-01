#!/usr/bin/env python3
"""Verdict mixte vs FX — rejoue les meilleurs trials de chaque study avec
decomposition metal/FX, puis holdout 26/10 (UNE fois par config choisie).

Usage serveur : cd /opt/arb/poc && python3 verdict_mixed.py [cand_mixed.npz cand_fxonly_current.npz]
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import optuna

sys.path.insert(0, "/opt/arb/poc")
import poc_engine as pe
from poc_optuna import (STORAGE, feasible_trials, day_of, TRAIN_DAYS,
                        HOLDOUT_DAY)

OUT = Path("/opt/arb/poc")
CAND_M = OUT / (sys.argv[1] if len(sys.argv) > 1 else "cand_mixed.npz")
CAND_F = OUT / (sys.argv[2] if len(sys.argv) > 2 else "cand_fxonly_current.npz")


def load_cand_file(path):
    z = np.load(path)
    by_day = {d: [] for d in pe.DAYS}
    for c in z["cands"]:
        d = day_of(int(c[0]))
        if d:
            by_day[d].append(tuple(c))
    return by_day


def replay_dec(eng, cands, p):
    """Copie fidèle de poc_optuna.replay + décomposition du PnL par nature
    du chemin argmax exécuté (métal vs FX pur)."""
    tot = dict(pnl=0.0, trades=0, aborts=0, wins=0, durs=[],
               pnl_m=0.0, n_m=0, pnl_f=0.0, n_f=0)
    t_free = -1e18
    for t, _pi0, _net0, _ddd0 in cands:
        if t < t_free:
            continue
        if _net0 < p["ddd_min"]:           # borne prouvée (cf. poc_optuna)
            continue
        eng.sync(int(t))
        ev = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"],
                         p["comm_scale"], rows=eng.IDX_FX)
        if ev is None or ev[0] < p["ddd_min"]:
            continue
        r = eng.exec_seq(int(t), ev[1], p["lat_ms"], p["tau_slip_bp"],
                         p["q_min"])
        if r is None:
            continue
        ism = bool(eng.P_ISM[ev[1]].any())
        tot["pnl"] += r["pnl"]; tot["trades"] += 1; tot["durs"].append(r["dur"])
        if ism:
            tot["pnl_m"] += r["pnl"]; tot["n_m"] += 1
        else:
            tot["pnl_f"] += r["pnl"]; tot["n_f"] += 1
        if r["pnl"] > 0:
            tot["wins"] += 1
        if r["res"] == "abort":
            tot["aborts"] += 1
        t_free = t + max(r["dur"], 1) + p["cooldown_s"] * 1000
    tot["wr"] = tot["wins"] / tot["trades"] if tot["trades"] else 0.0
    tot["dur_ms"] = float(np.mean(tot["durs"])) if tot["durs"] else 0.0
    del tot["durs"], tot["wins"]
    return tot


def pick(name, min_ddd=0.0):
    """Meilleur trial COMPLETE faisable avec ddd_min >= min_ddd."""
    st = optuna.load_study(study_name=name, storage=STORAGE)
    pool = [t for t in feasible_trials(st)
            if t.params["ddd_min"] >= min_ddd]
    return max(pool, key=lambda t: t.value) if pool else None


def report(tag, res):
    print("  %s : pnl=%.0f n=%d ab=%d wr=%.1f%% | metal n=%d pnl=%.0f | fx n=%d pnl=%.0f"
          % (tag, res["pnl"], res["trades"], res["aborts"], 100 * res["wr"],
             res["n_m"], res["pnl_m"], res["n_f"], res["pnl_f"]))


def main():
    eng_m = pe.Engine(syms=pe.FX + [ms for cs in pe.METAL_SYM.values()
                                    for ms in cs.values()], mixed=True)
    eng_f = pe.Engine(syms=pe.FX)
    cand_m = load_cand_file(CAND_M)
    cand_f = load_cand_file(CAND_F)
    out = {}
    for name, eng, cand in [("poc_exec_mixed", eng_m, cand_m),
                            ("poc_exec_fxcur", eng_f, cand_f)]:
        print("\n### %s" % name)
        for tag, mn in [("best", 0.0), ("op ddd>=2", 2.0), ("op ddd>=2.5", 2.5)]:
            t = pick(name, mn)
            if t is None:
                print("  %s : aucun trial faisable" % tag); continue
            p = t.params
            print(" trial #%d insample=%.0f %s" % (t.number, t.value, p))
            ins = dict(pnl=0.0, trades=0, aborts=0, pnl_m=0.0, n_m=0,
                       pnl_f=0.0, n_f=0, _w=0.0, _d=[])
            for d in TRAIN_DAYS:
                r = replay_dec(eng, cand[d], p)
                for k in ("pnl", "trades", "aborts", "pnl_m", "n_m",
                          "pnl_f", "n_f"):
                    ins[k] += r[k]
                ins["_w"] += r["wr"] * r["trades"]
            ins["wr"] = ins["_w"] / ins["trades"] if ins["trades"] else 0.0
            report("INSAMPLE 22+23", ins)
            if tag != "best":
                ho = replay_dec(eng, cand[HOLDOUT_DAY], p)
                report("HOLDOUT  26   ", ho)
                out[f"{name}:{tag}"] = dict(params=p, in_sample=t.value,
                                            insample_dec=ins, holdout=ho)
    (OUT / "verdict_mixed.json").write_text(json.dumps(out, indent=2))
    print("\n-> verdict_mixed.json")


if __name__ == "__main__":
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    main()
