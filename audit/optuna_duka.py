#!/usr/bin/env python3
"""Optuna v5 sur corpus Dukascopy — modele panier complet corrige.

Corpus : C:/arb_duka/data/<SYM>/<day>/<HH>.csv (ticks reels Dukascopy,
backup serveur 01/11). Engine PRECHARGE : Engine(root, days) — timeline
epoch ms globale, rembobinage par sync (avance/rembobine curseur).

Split temporel (regles etablies) :
  in-sample = 2026-10-22 + 2026-10-23 + 2026-10-26 (3 steps MedianPruner ;
              le 26 etait "brule" en holdout des anciennes campagnes —
              en in-sample il reste de la donnee valide)
  holdout   = 2026-10-27 — propre, JAMAIS utilise sous aucun modele,
              evalue UNE fois sur les params faisables optimaux

Latence : fixee 5 ms = scenario JForex/JCloud colocalise (propriete du
venue cible, pas un hyperparametre). comm_per_side = 0.40 $ conservateur
(Dukascopy ~35$/M standard ; comm_scale >= 1.0 dans l'espace).

Contraintes v5 (trial.set_constraint) : min_trades >= 10, abort <= 30 %.
TPE multivarie seed 42 + Random comparaison ; enqueue defaults ;
SQLite optuna_duka.db + load_if_exists ; PedAnova post-study.

Verrous identiques au live et au replay cTrader : warm-up 100 ticks/
symbole, lock "une quote = un trade" sur le panier complet, t_free =
min_seq_gap + max_hold.

Usage : python optuna_duka.py [n_trials] [n_random]
"""
import sys
import time
from pathlib import Path

import numpy as np
import opengraph
import optuna

sys.path.insert(0, str(Path(__file__).parent))
import poc_engine as pe  # noqa: E402

FX = pe.FX
CURR = pe.CURR
N = len(FX)

ROOT = r"C:\arb_duka\data"
DAYS_ALL = ["2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
IN_SAMPLE = ["2026-10-22", "2026-10-23", "2026-10-26"]
HOLDOUT = ["2026-10-27"]

DB = "sqlite:///" + str(Path(__file__).parent / "optuna_duka.db")
STUDY_TPE = "duka_tpe"
STUDY_RND = "duka_rand"

COMM_SIDE = 0.40
LAT_MS = 5.0            # colocation JForex/JCloud — fixe, pas optimise
MIN_SEQ_GAP_MS = 2000
MAX_HOLD_MS = 5000
WARMUP = 100
MIN_TRADES = 10
MAX_ABORT = 0.30
EVAL_EVERY_MS = 500      # convention Mo7 : instants echantillonnes
                         # (le live evalue a chaque tick — la frequence
                         # detect est indicative seulement)

LIVE_DEFAULTS = dict(fresh_ms=8000.0, spread_cap=1.76, comm_scale=1.10,
                     ddd_min=0.4, tau_slip_bp=2.08, q_min=0.5)

_ENG = None
_DAYDATA = {}  # day -> (ev_ts, sym_ts_list, seen_min)


def engine():
    global _ENG
    if _ENG is None:
        _ENG = opengraph.Engine(ROOT, DAYS_ALL)
        _ENG.set_comm_per_side(COMM_SIDE)
    return _ENG


def _epoch_ms(day):
    import datetime as dt
    y, m, dd = map(int, day.split("-"))
    return int(dt.datetime(y, m, dd, tzinfo=dt.timezone.utc).timestamp() * 1000)


def day_data(day):
    """(events_ts uniques, ts par symbole, nb ticks vus min) — calcule une fois."""
    if day in _DAYDATA:
        return _DAYDATA[day]
    import pandas as pd
    root = Path(ROOT)
    t0 = _epoch_ms(day)
    sym_ts = []
    tss = []
    for sym in FX:
        d = root / sym / day
        parts = []
        if d.is_dir():
            for f in sorted(d.glob("*.csv")):
                arr = pd.read_csv(f, usecols=[0]).iloc[:, 0].to_numpy(
                    dtype=np.int64)
                parts.append(arr + t0 + int(f.stem) * 3_600_000)
        ts = np.unique(np.concatenate(parts)) if parts \
            else np.array([], dtype=np.int64)
        sym_ts.append(ts)
        tss.append(ts)
    ev = np.unique(np.concatenate(tss)) if tss else np.array([], dtype=np.int64)
    seen_min = (np.minimum.reduce(
        [np.searchsorted(ts, ev) for ts in sym_ts]) if len(ev)
        else np.array([], dtype=np.int64))
    _DAYDATA[day] = (ev, sym_ts, seen_min)
    return _DAYDATA[day]


def basket_edges(eng, pi):
    es = set(FX.index(s) for s in eng.legs(pi))
    s0 = CURR.index(eng.start(pi))
    end = eng.end_node(pi)
    for c in (s0, end):
        if c != 0:
            for nm in (CURR[c] + "USD", "USD" + CURR[c]):
                if nm in FX:
                    es.add(FX.index(nm))
                    break
    return es


def replay_day(day, p):
    eng = engine()
    ev_ts, tick_ts, seen_min = day_data(day)
    lock_until = np.full(N, -1e18)
    t_free = -1e18
    last_eval = -1e18
    pnl = trades = aborts = 0.0
    for i in range(len(ev_ts)):
        t = int(ev_ts[i])
        if seen_min[i] < WARMUP or t - last_eval < EVAL_EVERY_MS:
            continue
        last_eval = t
        ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"],
                         p["comm_scale"])
        if ev is None:
            continue
        pi, net, _ddd = ev
        if net < p["ddd_min"] or t < t_free:
            continue
        bes = basket_edges(eng, pi)
        if any(lock_until[x] > t for x in bes):
            continue
        r = eng.exec_seq(int(t), pi, LAT_MS, p["tau_slip_bp"],
                         p["q_min"], False)
        for x in bes:
            ts = tick_ts[x]
            j = int(np.searchsorted(ts, t, "right"))
            lock_until[x] = ts[j] if j < len(ts) else 1e18
        t_free = t + MIN_SEQ_GAP_MS + MAX_HOLD_MS
        if r is None:
            continue
        res, pnl_r, _f, _parts, _dur = r
        trades += 1
        pnl += pnl_r
        if str(res).startswith("abort"):
            aborts += 1
    return pnl, int(trades), int(aborts)


def objective(trial):
    p = dict(
        fresh_ms=trial.suggest_float("fresh_ms", 2_000.0, 30_000.0, log=True),
        spread_cap=trial.suggest_float("spread_cap", 1.0, 3.0),
        comm_scale=trial.suggest_float("comm_scale", 1.0, 1.3),
        ddd_min=trial.suggest_float("ddd_min", 0.0, 1.0),
        tau_slip_bp=trial.suggest_float("tau_slip_bp", 1.0, 4.0),
        q_min=trial.suggest_float("q_min", 0.3, 0.9),
    )
    cum = trades = aborts = 0.0
    for step, day in enumerate(IN_SAMPLE):
        d_pnl, d_tr, d_ab = replay_day(day, p)
        cum += d_pnl
        trades += d_tr
        aborts += d_ab
        trial.report(cum, step)
        if trial.should_prune():
            raise optuna.TrialPruned()
    ar = float(aborts / trades) if trades else 1.0
    trial.set_user_attr("trades", int(trades))
    trial.set_user_attr("abort_rate", ar)
    trial.set_user_attr("params_used", p)
    trial.set_constraint("min_trades", MIN_TRADES - trades)
    trial.set_constraint("abort_rate", ar - MAX_ABORT)
    return float(cum)


def feasible_trials(study):
    return [t for t in study.trials
            if t.value is not None
            and t.user_attrs.get("trades", 0) >= MIN_TRADES
            and t.user_attrs.get("abort_rate", 1.0) <= MAX_ABORT]


def eval_oos(params, days, label):
    pnl = trades = aborts = 0.0
    for day in days:
        d_pnl, d_tr, d_ab = replay_day(day, params)
        pnl += d_pnl
        trades += d_tr
        aborts += d_ab
    ar = aborts / trades if trades else 0.0
    print(f"  [{label}] pnl={pnl:+.2f}$ trades={trades} aborts={aborts} "
          f"rate={ar:.0%}", flush=True)
    return dict(pnl=float(pnl), trades=int(trades), aborts=int(aborts),
                abort_rate=float(ar))


def main():
    n_tpe = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    n_rnd = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    t0 = time.time()

    engine()  # preload une fois
    for day in DAYS_ALL:
        ev, _, _ = day_data(day)
        print(f"[events] {day} : {len(ev)}", flush=True)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    common = dict(storage=DB, load_if_exists=True, directions=["maximize"])

    tpe = optuna.create_study(
        study_name=STUDY_TPE,
        sampler=optuna.samplers.TPESampler(multivariate=True, seed=42),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10,
                                         n_warmup_steps=0),
        **common)
    tpe.enqueue_trial(LIVE_DEFAULTS)
    print(f"[tpe] {n_tpe} trials...", flush=True)
    tpe.optimize(objective, n_trials=n_tpe, n_jobs=1, gc_after_trial=True)

    rnd = optuna.create_study(
        study_name=STUDY_RND,
        sampler=optuna.samplers.RandomSampler(seed=42), **common)
    rnd.enqueue_trial(LIVE_DEFAULTS)
    print(f"[rand] {n_rnd} trials...", flush=True)
    rnd.optimize(objective, n_trials=n_rnd, n_jobs=1, gc_after_trial=True)

    out = {"db": DB, "elapsed_s": time.time() - t0,
           "in_sample": IN_SAMPLE, "holdout": HOLDOUT,
           "lat_ms": LAT_MS, "studies": {}}
    for name, st in (("tpe", tpe), ("rand", rnd)):
        feas = feasible_trials(st)
        trials = [t for t in st.trials if t.value is not None]
        rec = dict(n_trials=len(trials), n_feasible=len(feas),
                   feasible=[dict(value=t.value, params=t.params,
                                  trades=t.user_attrs["trades"],
                                  abort_rate=t.user_attrs["abort_rate"])
                             for t in sorted(feas, key=lambda x: -x.value)])
        out["studies"][name] = rec
        print(f"[{name}] trials={len(trials)} feasible={len(feas)}")
        for t in rec["feasible"][:5]:
            print(f"   pnl={t['value']:+.2f}$ tr={t['trades']} "
                  f"ar={t['abort_rate']:.0%} {t['params']}")

    feas_tpe = feasible_trials(tpe)
    if feas_tpe:
        best = max(feas_tpe, key=lambda t: t.value)
        bp = best.user_attrs["params_used"]
        print(f"\n[best tpe] #{best.number} pnl={best.value:+.2f}$ {bp}")
        out["best"] = dict(number=best.number, value=best.value, params=bp)
        print("[holdout 2026-10-27 — EVALUE UNE FOIS]")
        out["holdout_res"] = eval_oos(bp, HOLDOUT, "holdout")
        print("[live defaults — meme holdout, reference]")
        out["live_holdout"] = eval_oos(dict(LIVE_DEFAULTS), HOLDOUT,
                                     "live@holdout")
    else:
        print("\n[best] AUCUN essai realisable — region faisable vide")
        out["best"] = None

    try:
        from optuna.importance import PedAnovaImportanceEvaluator
        done = [t for t in tpe.trials if t.value is not None]
        if len(done) >= 10:
            imp = optuna.importance.get_param_importances(
                tpe, evaluator=PedAnovaImportanceEvaluator())
            out["pedanova"] = imp
            print("[pedanova]", {k: round(v, 3) for k, v in imp.items()})
    except Exception as ex:  # noqa: BLE001
        out["pedanova"] = f"erreur: {ex}"
        print("[pedanova] erreur:", ex)

    dst = Path(__file__).parent.parent / "MC" / "optuna_duka.json"
    dst.write_text(__import__("json").dumps(out, indent=1, default=str))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
