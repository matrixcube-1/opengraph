#!/usr/bin/env python3
"""Optuna v5 sur ticks REELS cTrader — modèle panier complet corrigé.

Corpus : journaux cBot réels (quotes des 28 paires).
  In-sample (steps MedianPruner) : 2026-11-02 + 2026-11-03 (cs_journal)
  Holdout evalue UNE fois        : 2026-11-04 (cs_journal)
  Second OOS descriptif          : 2026-11-10/11 (arb_og_trade, jamais touche)

Regles appliquees (AGENTS.md — inventaire Optuna v5) :
  - split temporel strict : les jours in-sample sont les steps de
    MedianPruner ; le holdout est evalue UNE fois, a la fin
  - constraints_func : min_trades >= 10 et abort_rate <= 30 %
  - enqueue_trial des defauts live (les params de l'instance cTrader)
  - study TPE multivarie seed fixe n_jobs=1 + study RandomSampler de
    comparaison (meme objectif, meme budget reduit)
  - SQLite optuna_ctrader_reel.db + load_if_exists=True
  - PedAnova post-study
  - Hawkes MLE : HORS Optuna (non inclus ici)

Espace de recherche : fresh_ms, spread_cap, comm_scale, ddd_min,
tau_slip_bp, q_min. La latence (280 ms mesuree cTrader) est une
propriete du venue, PAS un hyperparametre — fixee. comm_per_side
0.40 $ mesure — fixe (comm_scale >= 1.0 : on ne calibre pas sur un
taux non confirme).

Objectif : PnL execute total in-sample (exec_seq deterministe, K=1,
lat=280) — pas de tirage MC dans l'objectif (deterministe + rapide).

Reutilise l'ingestion par rembobinage : un Engine.live() par JOUR,
ingere une fois ; chaque essai re-sweep les events via eval_at(sync).
Verrous identiques au replay MC : warm-up 100 ticks/symbole, lock
"une quote = un trade" sur le panier complet, t_free = gap + hold.

Usage : python optuna_ctrader_reel.py [n_trials] [n_random]
"""
import sys
import time
from pathlib import Path

import numpy as np
import opengraph
import optuna

sys.path.insert(0, str(Path(__file__).parent))
import mc_ctrader_reel as M  # noqa: E402
import poc_engine as pe     # noqa: E402

N = M.N
FX = pe.FX
CURR = pe.CURR

DB = "sqlite:///" + str(Path(__file__).parent / "optuna_ctrader_reel.db")
STUDY_TPE = "ctrader_reel_tpe"
STUDY_RND = "ctrader_reel_rand"

# --- split temporel -------------------------------------------------
IN_SAMPLE = [r"C:\arb_cs_journal\2026-11-02.jsonl",
             r"C:\arb_cs_journal\2026-11-03.jsonl"]
HOLDOUT = [r"C:\arb_cs_journal\2026-11-04.jsonl"]
OOS2 = r"C:\arb_og_trade\2026-11-*.jsonl"

# --- constantes venue (non optimisees) ------------------------------
COMM_SIDE = 0.40
LAT_MS = 280.0          # wire 250-330 mesure + traitement
MIN_SEQ_GAP_MS = 2000
MAX_HOLD_MS = 5000
WARMUP = 100
MIN_TRADES = 10
MAX_ABORT = 0.30

# defauts = params de l'instance live (boot 08:11)
LIVE_DEFAULTS = dict(fresh_ms=8000.0, spread_cap=1.76, comm_scale=1.10,
                     ddd_min=0.4, tau_slip_bp=2.08, q_min=0.5)

_DAYS = {}  # cache : fichier -> (ticks, events, engine)


def day_engine(path):
    """Engine.live() ingere une fois par jour (avance/rembobine via sync)."""
    if path not in _DAYS:
        ticks, events = M.load_journal(path)
        eng = opengraph.Engine.live()
        eng.set_comm_per_side(COMM_SIDE)
        for e in range(N):
            ts, bid, ask = ticks[e]
            for i in range(len(ts)):
                eng.on_tick(e, int(ts[i]), float(bid[i]), float(ask[i]),
                            1.0, 8000.0, 99.0, 1.0)
        _DAYS[path] = (ticks, events, eng)
    return _DAYS[path]


def basket_edges(eng, pi):
    legs = [FX.index(s) for s in eng.legs(pi)]
    es = set(legs)
    s0 = CURR.index(eng.start(pi))
    end = eng.end_node(pi)
    for c in (s0, end):
        if c != 0:
            for nm in (CURR[c] + "USD", "USD" + CURR[c]):
                if nm in FX:
                    es.add(FX.index(nm))
                    break
    return es


def replay_day(path, p):
    """Un essai sur un jour : sweep eval + exec_seq sur les candidats."""
    ticks, events, eng = day_engine(path)
    n_ticks = np.zeros(N, dtype=np.int64)
    lock_until = np.full(N, -1e18)
    tick_ts = {e: ticks[e][0] for e in range(N)}
    t_free = -1e18
    last_t = -1
    pnl = trades = aborts = 0.0
    for t, e in events:
        n_ticks[e] += 1
        if t == last_t:
            continue
        last_t = t
        ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"], p["comm_scale"])
        if ev is None:
            continue
        pi, net, _ddd = ev
        if net < p["ddd_min"] or t < t_free or (n_ticks < WARMUP).any():
            continue
        bes = basket_edges(eng, pi)
        if any(lock_until[x] > t for x in bes):
            continue
        r = eng.exec_seq(t, pi, LAT_MS, p["tau_slip_bp"], p["q_min"], False)
        for x in bes:
            j = int(np.searchsorted(tick_ts[x], t, "right"))
            lock_until[x] = tick_ts[x][j] if j < len(tick_ts[x]) else 1e18
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
    for step, path in enumerate(IN_SAMPLE):
        d_pnl, d_tr, d_ab = replay_day(path, p)
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
    # contraintes v5 : realisable <=> toutes les valeurs <= 0
    trial.set_constraint("min_trades", MIN_TRADES - trades)
    trial.set_constraint("abort_rate", ar - MAX_ABORT)
    return float(cum)


def feasible_trials(study):
    return [t for t in study.trials
            if t.value is not None
            and t.user_attrs.get("trades", 0) >= MIN_TRADES
            and t.user_attrs.get("abort_rate", 1.0) <= MAX_ABORT]


def eval_oos(params, paths, label):
    pnl = trades = aborts = 0.0
    for path in paths:
        d_pnl, d_tr, d_ab = replay_day(path, params)
        pnl += d_pnl
        trades += d_tr
        aborts += d_ab
    ar = aborts / trades if trades else 0.0
    print(f"  [{label}] pnl={pnl:+.2f}$ trades={trades} aborts={aborts} "
          f"rate={ar:.0%}")
    return dict(pnl=float(pnl), trades=int(trades), aborts=int(aborts),
                abort_rate=float(ar))


def main():
    n_tpe = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    n_rnd = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    t0 = time.time()

    # pre-ingestion des jours (une fois)
    for path in IN_SAMPLE + HOLDOUT:
        _, ev, _ = day_engine(path)
        print(f"[ingest] {Path(path).name} : {len(ev)} events", flush=True)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    common = dict(storage=DB, load_if_exists=True,
                  directions=["maximize"])

    tpe = optuna.create_study(
        study_name=STUDY_TPE, sampler=optuna.samplers.TPESampler(
            multivariate=True, seed=42),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10,
                                         n_warmup_steps=0),
        **common)
    tpe.enqueue_trial(LIVE_DEFAULTS)
    print(f"[tpe] {n_tpe} trials...", flush=True)
    tpe.optimize(objective, n_trials=n_tpe, n_jobs=1, gc_after_trial=True)

    rnd = optuna.create_study(
        study_name=STUDY_RND, sampler=optuna.samplers.RandomSampler(seed=42),
        **common)
    rnd.enqueue_trial(LIVE_DEFAULTS)
    print(f"[rand] {n_rnd} trials...", flush=True)
    rnd.optimize(objective, n_trials=n_rnd, n_jobs=1, gc_after_trial=True)

    # --- resultats --------------------------------------------------
    out = {"db": DB, "elapsed_s": time.time() - t0, "in_sample": IN_SAMPLE,
           "holdout": HOLDOUT, "oos2": OOS2, "studies": {}}
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
        print("[holdout 11-04 — EVALUE UNE FOIS]")
        out["holdout_res"] = eval_oos(bp, HOLDOUT, "holdout")
        print("[oos2 og_trade 10-11 — descriptif]")
        out["oos2_res"] = eval_oos(bp, OOS2, "oos2")
        bp_live = dict(LIVE_DEFAULTS)
        print("[live defaults — meme holdout, reference]")
        out["live_holdout"] = eval_oos(bp_live, HOLDOUT, "live@holdout")
    else:
        print("\n[best] AUCUN essai realisable (min_trades>=10, "
              "abort<=30%) — region faisable vide")
        out["best"] = None

    # PedAnova post-study (sur les essais termines)
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

    dst = Path(__file__).parent.parent / "MC" / "optuna_ctrader_reel.json"
    dst.write_text(__import__("json").dumps(out, indent=1, default=str))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
