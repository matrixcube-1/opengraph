#!/usr/bin/env python3
"""optuna_journal.py — retune minimal sur le corpus REEL cTrader.

Espace volontairement reduit (4 parametres de decision, rien d'autre) :
  fresh_ms, spread_cap, ddd_min, tau_slip_bp
Physique fixee au scenario ECN : lat=1.0 ms, comm=1.00 $/cote,
comm_scale=1.10, q_min=0.5.

Objectif : PnL total realise (exec_seq) sur les episodes detectes,
avec contrainte douce >= MIN_TRADES executions (sinon un seuil haut
gagne sur 2 trades chanceux).

TPE multivarie, seed fixe, SQLite -> reprise possible.
Usage : python optuna_journal.py [--trials 200] [--max-exec 400]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import poc_engine as pe                     # noqa: E402
import opengraph as og                      # noqa: E402
import optuna                               # noqa: E402

JOURNALS = [
    "C:/arb_cs_journal/2026-11-02.jsonl",
    "C:/arb_cs_journal/2026-11-03.jsonl",
]
LAT, COMM, SCALE, QMIN = 1.0, 1.00, 1.10, 0.5
MIN_TRADES = 30

print("chargement des ticks...", flush=True)
TICKS = []
for f in JOURNALS:
    for line in open(f, encoding="utf-8"):
        if '"tick"' not in line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("ev") != "tick":
            continue
        TICKS.append((int(ev["e"]), int(ev["ts"]),
                      float(ev["bid"]), float(ev["ask"])))
print(f"{len(TICKS):,} ticks charges", flush=True)


def run(fresh, cap, ddd, tau, max_exec):
    eng = og.Engine.live()
    eng.set_comm_per_side(COMM)
    opps = []
    for e, t, b, a in TICKS:
        r = eng.on_tick(e, t, bid=b, ask=a, vol=0.0,
                        fresh_ms=fresh, spread_cap=cap, comm_scale=SCALE)
        if r is not None and r[1] >= ddd:
            opps.append((t, r[0], r[1]))
    # episodes : silence > fresh sur le meme pi
    episodes = []
    for t, pi, net in opps:
        if episodes and episodes[-1][0] == pi and t - episodes[-1][2] <= fresh:
            ep = episodes[-1]
            ep[2] = t
            if net > ep[3]:
                ep[3], ep[4] = net, t
        else:
            episodes.append([pi, t, t, net, t])
    pnls = []
    aborts = 0
    for ep in episodes[:max_exec]:
        res, pnl, fills, parts, dur = eng.exec_seq(
            ep[4], ep[0], lat_ms=LAT, tau_slip_bp=tau, q_min=QMIN,
            vol_guard=False)
        pnls.append(pnl)
        if res != "done":
            aborts += 1
    return sum(pnls), len(pnls), len(episodes), aborts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--max-exec", type=int, default=400)
    args = ap.parse_args()

    def objective(trial):
        fresh = trial.suggest_float("fresh_ms", 200.0, 2000.0)
        cap = trial.suggest_float("spread_cap", 1.2, 3.0)
        ddd = trial.suggest_float("ddd_min", 1.0, 6.0)
        tau = trial.suggest_float("tau_slip_bp", 0.3, 1.5)
        total, n_exec, n_ep, ab = run(fresh, cap, ddd, tau, args.max_exec)
        trial.set_user_attr("n_exec", n_exec)
        trial.set_user_attr("n_episodes", n_ep)
        trial.set_user_attr("aborts", ab)
        if n_exec < MIN_TRADES:
            return total - 50.0 * (MIN_TRADES - n_exec)
        return total

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=7, multivariate=True),
        storage="sqlite:///optuna_journal.db",
        study_name="journal_ecn",
        load_if_exists=True)
    study.enqueue_trial({"fresh_ms": 909.0, "spread_cap": 1.76,
                         "ddd_min": 2.66, "tau_slip_bp": 0.69})
    study.optimize(objective, n_trials=args.trials, n_jobs=1,
                   show_progress_bar=False)

    b = study.best_trial
    print("\n=== BEST ===")
    print(f"value={b.value:+.2f}$  params={b.params}  attrs={b.user_attrs}")
    print("\nTop 5 :")
    for t in sorted(study.trials, key=lambda x: x.value or -1e18,
                    reverse=True)[:5]:
        print(f"  #{t.number} {t.value:+.2f}$ {t.params} "
              f"execs={t.user_attrs.get('n_exec')}")


if __name__ == "__main__":
    main()
