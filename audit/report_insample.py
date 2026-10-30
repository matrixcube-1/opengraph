"""Rapport intermédiaire in-sample — POC arbitrage FX (Convention A, FX pur).
Exécuté sur le serveur /opt/arb/poc. Produit un JSON.
Sections : candidats/épisodes (cand.npz) | détail exec_seq (replay best params).
"""
import json
from collections import Counter, defaultdict

import numpy as np

import poc_engine as E
from poc_optuna import load_cands

PIP = {s: (0.01 if s.endswith("JPY") else 0.0001) for s in E.FX}

def q(v, ps=(5, 25, 50, 75, 90, 95, 99)):
    a = np.asarray(v, float)
    return {f"p{p}": float(np.percentile(a, p)) for p in ps}

def main():
    out = {}
    paths, _ = E.enum_paths()
    # ---------- A. candidats & épisodes (cand.npz déjà produit) ----------
    d = np.load("cand.npz")
    cands, eps = d["cands"], d["episodes"]
    net, pi = cands[:, 2], cands[:, 1].astype(int)
    nlegs = np.array([len([e for e in paths[p] if not isinstance(e, tuple)])
                      for p in pi])
    out["cands_total"] = int(len(cands))
    out["net_usd"] = q(net, (50, 90, 99, 99.9))
    out["net_usd"]["max"] = float(net.max())
    out["net_bp"] = q(net / 1e5 * 1e4, (50, 90, 99))
    for th in (0, 1, 5, 10, 20, 50):
        out[f"net_gt_{th}"] = int((net > th).sum())
    out["legs_hist"] = {int(k): int(v)
                        for k, v in zip(*np.unique(nlegs, return_counts=True))}
    # ---------- moteur + up[] pour conversions pip ----------
    eng = E.Engine()
    eng.sync(int(cands[len(cands) // 2, 0]))
    up = eng.usd_price(np.ones(len(eng.slist), bool))
    # marge par paire terminale (pips propres à la paire)
    per_pair = defaultdict(list)
    for i in range(len(cands)):
        legs = [e for e in paths[pi[i]] if not isinstance(e, tuple)]
        per_pair[E.FX[legs[-1]]].append(net[i])
    rows = []
    for s, vals in sorted(per_pair.items(), key=lambda kv: -len(kv[1])):
        b, qc = eng.fe[s]
        pv = 1e5 * PIP[s] * up[qc] / up[b]           # $ par pip / 100 K$ notional
        rows.append({"pair": s, "n": len(vals),
                     "med_usd": round(float(np.median(vals)), 2),
                     "med_pips": round(float(np.median(vals) / pv), 2)})
    out["per_term_pair"] = rows
    # épisodes : >1 h = artefact forward-fill (week-end/trou de marché)
    durs = (eps[:, 1] - eps[:, 0]) / 1000.0
    ok = durs <= 3600
    out["episodes"] = {"n": int(len(eps)),
                      "dur_s": q(durs[ok], (50, 90, 99)),
                      "max_ok_s": round(float(durs[ok].max()), 1),
                      "artifacts_gt1h": int((~ok).sum()),
                      "artifact_max_s": round(float(durs.max()), 1)}
    # ---------- B. replay détaillé best params (jours in-sample) ----------
    best = json.load(open("best_params.json"))["params"]
    cbd, _ = load_cands()
    details = defaultdict(lambda: dict(n=0, pnl=0.0, wins=0, aborts=Counter(),
                                       fills=Counter(), parts=0, durs=[]))
    per_legs = defaultdict(lambda: dict(n=0, pnl=0.0, wins=0, aborts=0, durs=[]))
    tot = dict(n=0, pnl=0.0, wins=0, aborts=0, parts=0)
    for day in ("2026-10-22", "2026-10-23"):
        t_free = -1e18
        for t, _pi0, _n0, _d0 in cbd[day]:
            if t < t_free:
                continue
            eng.sync(int(t))
            ev = eng.eval_at(int(t), best["fresh_ms"], best["spread_cap"],
                             best["comm_scale"], rows=eng.IDX_FX)
            if ev is not None and ev[0] >= best["ddd_min"]:
                ev2 = eng.eval_at(int(t), best["fresh_ms"], best["spread_cap"],
                                  best["comm_scale"])
                if ev2 and ev2[0] > ev[0]:
                    ev = ev2
            if ev is None or ev[0] < best["ddd_min"]:
                continue
            r = eng.exec_seq(int(t), ev[1], best["lat_ms"], best["tau_slip_bp"],
                             best["q_min"])
            if r is None:
                continue
            nl = len([e for e in paths[ev[1]] if not isinstance(e, tuple)])
            g = details[day]; L = per_legs[nl]
            for bag in (g, L, tot):
                bag["n"] += 1; bag["pnl"] += r["pnl"]
            if r["pnl"] > 0:
                g["wins"] += 1; L["wins"] += 1; tot["wins"] += 1
            if r["res"] == "abort":
                g["aborts"][r["why"]] += 1; L["aborts"] += 1; tot["aborts"] += 1
            g["fills"][r["fills"]] += 1
            g["parts"] += r.get("parts", 0); tot["parts"] += r.get("parts", 0)
            g["durs"].append(r["dur"]); L["durs"].append(r["dur"])
            t_free = t + max(r["dur"], 1) + best["cooldown_s"] * 1000
    out["replay_params"] = best
    out["replay"] = {
        day: dict(n=g["n"], pnl=round(g["pnl"], 2),
                  wr=round(g["wins"] / max(g["n"], 1), 4),
                  aborts=dict(g["aborts"]), fills_hist=dict(g["fills"]),
                  partial_legs=g["parts"],
                  dur_ms=q(g["durs"], (50, 90, 99)) if g["durs"] else {})
        for day, g in details.items()}
    out["per_legcount"] = {
        k: dict(n=v["n"], pnl=round(v["pnl"], 2),
                wr=round(v["wins"] / max(v["n"], 1), 4), aborts=v["aborts"],
                dur_ms_med=round(float(np.median(v["durs"])), 2))
        for k, v in sorted(per_legs.items())}
    out["replay_tot"] = {k: (round(v, 2) if isinstance(v, float) else v)
                         for k, v in tot.items()}
    print(json.dumps(out, indent=1, default=str))

if __name__ == "__main__":
    main()
