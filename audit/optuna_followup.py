#!/usr/bin/env python3
"""optuna_followup.py — relance 05:00 : verifie l'etude journal_ecn et,
si le best est colle a la borne haute de ddd_min, lance une extension
ciblee (ddd_min elargi a 12) seedee par les top trials. Sinon, ecrit
juste le verdict dans le log. Sortie : optuna_followup.log"""
import json
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opengraph as og                      # noqa: E402
import optuna                               # noqa: E402

DB = "sqlite:///optuna_journal.db"
LOG = "optuna_followup.log"
JOURNALS = [
    "C:/arb_cs_journal/2026-11-02.jsonl",
    "C:/arb_cs_journal/2026-11-03.jsonl",
    "C:/arb_cs_journal/2026-11-04.jsonl",
]
LAT, COMM, SCALE, QMIN = 1.0, 1.00, 1.10, 0.5
MIN_TRADES = 30
BOUND_HI = 6.0          # borne haute de la 1re etude
EXTEND_IF = 5.6         # best > 5.6 => colle a la borne -> extension
DDD_MAX2 = 12.0
TRIALS2 = 60
MAX_EXEC = 400


def log(msg):
    line = f"{datetime.datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_ticks():
    ticks = []
    for f in JOURNALS:
        try:
            fh = open(f, encoding="utf-8")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                if '"tick"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("ev") != "tick":
                    continue
                ticks.append((int(ev["e"]), int(ev["ts"]),
                              float(ev["bid"]), float(ev["ask"])))
    return ticks


def run(ticks, fresh, cap, ddd, tau, max_exec):
    eng = og.Engine.live()
    eng.set_comm_per_side(COMM)
    opps = []
    for e, t, b, a in ticks:
        r = eng.on_tick(e, t, bid=b, ask=a, vol=0.0,
                        fresh_ms=fresh, spread_cap=cap, comm_scale=SCALE)
        if r is not None and r[1] >= ddd:
            opps.append((t, r[0], r[1]))
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
    for ep in episodes[:max_exec]:
        res, pnl, *_ = eng.exec_seq(
            ep[4], ep[0], lat_ms=LAT, tau_slip_bp=tau, q_min=QMIN,
            vol_guard=False)
        pnls.append(pnl)
    return sum(pnls), len(pnls), len(episodes)


def main():
    log("=== followup 05:00 ===")
    try:
        s1 = optuna.load_study(study_name="journal_ecn", storage=DB)
    except Exception as e:
        log(f"ECHEC chargement etude: {e}")
        return
    done = [t for t in s1.trials if t.state.name == "COMPLETE"]
    log(f"etude 1: {len(done)}/200 trials complets")
    if not done:
        log("aucun trial complet — rien a faire")
        return
    b = s1.best_trial
    log(f"best #{b.number} = {b.value:+.2f}$  params={b.params}")
    for t in sorted(done, key=lambda x: x.value or -1e18,
                    reverse=True)[:5]:
        log(f"  top #{t.number} {t.value:+.2f}$ {t.params}")

    ddd_best = b.params["ddd_min"]
    if ddd_best < EXTEND_IF:
        log(f"ddd_min best={ddd_best:.2f} < {EXTEND_IF} — "
            "pas colle a la borne, PAS d'extension. Verdict: seuil "
            "interne, l'etude est concluante.")
        return

    log(f"ddd_min best={ddd_best:.2f} -> colle a {BOUND_HI}, "
        f"EXTENSION: ddd_min -> [1,{DDD_MAX2}], {TRIALS2} trials")
    ticks = load_ticks()
    log(f"{len(ticks):,} ticks")

    def objective(trial):
        fresh = trial.suggest_float("fresh_ms", 200.0, 2000.0)
        cap = trial.suggest_float("spread_cap", 1.2, 3.0)
        ddd = trial.suggest_float("ddd_min", 1.0, DDD_MAX2)
        tau = trial.suggest_float("tau_slip_bp", 0.3, 1.5)
        total, n_exec, n_ep = run(ticks, fresh, cap, ddd, tau, MAX_EXEC)
        trial.set_user_attr("n_exec", n_exec)
        trial.set_user_attr("n_episodes", n_ep)
        if n_exec < MIN_TRADES:
            return total - 50.0 * (MIN_TRADES - n_exec)
        return total

    s2 = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=11, multivariate=True),
        storage=DB, study_name="journal_ecn_wide", load_if_exists=True)
    for t in sorted(done, key=lambda x: x.value or -1e18,
                    reverse=True)[:10]:
        s2.enqueue_trial(t.params)
    s2.optimize(objective, n_trials=TRIALS2, n_jobs=1)
    b2 = s2.best_trial
    log(f"=== BEST extension: {b2.value:+.2f}$ {b2.params} "
        f"attrs={b2.user_attrs} ===")


if __name__ == "__main__":
    main()
