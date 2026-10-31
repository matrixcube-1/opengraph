#!/usr/bin/env python3
"""POC — re-tuning Optuna v2 : objectif lexicographique « aucun jour perdant ».

Variante de poc_optuna.py qui corrige le biais d'agregation identifie dans
l'audit du seuil : l'objectif brut somme(PnL_jour) laisse le 22/10 (+48 k$)
dominer et accepte les jours deficitaires -> best historique ddd_min = 0.

Changement vs v2 :
- 3 jours in-sample (22/23/26 oct. 2026) au lieu de 2 — le 27 reste intouchable.
- Contrainte hard supplementaire : no_losing_day (min_d PnL_d >= 0).
  La maximisation de total s'opere alors uniquement parmi les configurations
  sans jour deficitaire — lexicographique exact, aucune ponderation arbitraire.
- Fallback documente : si aucun trial realisable, meilleur compromis =
  max(worst_day) puis max(total).
- Ecrit best_params_pen.json — ne touche NI best_params.json NI holdout.json.

Usage :
  python3 poc_optuna_pen.py smoke      # 10 trials TPE
  python3 poc_optuna_pen.py run 150    # campagne (reprise via sqlite)
  python3 poc_optuna_pen.py eval '{...}'   # rejeu 3 jours d'un dict params
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import optuna

sys.path.insert(0, str(Path(__file__).parent))
from poc_engine import Engine, DAYS, EPOCH

OUT = Path("/opt/arb/poc")
STORAGE = f"sqlite:///{OUT}/optuna.db"
TRAIN_DAYS = DAYS[:]                      # 22, 23, 26 oct. -> in-sample (3 steps)
SEED = 20261107

DOC_DEFAULTS = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
                    lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)


def day_of(t_ms):
    for d, e0 in EPOCH.items():
        if e0 <= t_ms < e0 + 86_400_000:
            return d
    return None


def load_cands():
    z = np.load(OUT / "cand.npz")
    cands = [tuple(c) for c in z["cands"]]
    ts = np.asarray([c[0] for c in cands])
    assert np.all(np.diff(ts) >= 0), "cand.npz : timestamps non croissants"
    by_day = {d: [] for d in DAYS}
    for c in cands:
        d = day_of(int(c[0]))
        if d:
            by_day[d].append(c)
    return by_day


def replay(eng, cands, p):
    """Identique a poc_optuna.replay — Convention A, serialisation t_free."""
    pnl = 0.0; n = 0; aborts = 0; wins = 0; durs = []
    t_free = -1e18
    for t, _pi0, _net0, _ddd0 in cands:
        if t < t_free:
            continue
        eng.sync(int(t))
        ev = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"], p["comm_scale"],
                         rows=eng.IDX_FX)
        if ev is not None and ev[0] >= p["ddd_min"]:
            ev2 = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"], p["comm_scale"])
            assert ev2 is None or ev2[0] <= ev[0], \
                "chemin bout argmax — Convention A remise en cause"
        if ev is None or ev[0] < p["ddd_min"]:
            continue
        r = eng.exec_seq(int(t), ev[1], p["lat_ms"], p["tau_slip_bp"], p["q_min"])
        if r is None:
            continue
        if r["res"] == "abort":
            aborts += 1
        if r["pnl"] > 0:
            wins += 1
        pnl += r["pnl"]; n += 1; durs.append(r["dur"])
        t_free = t + max(r["dur"], 1) + p["cooldown_s"] * 1000
    return dict(pnl=pnl, trades=n, aborts=aborts,
                wr=(wins / n if n else 0.0),
                dur_ms=float(np.mean(durs)) if durs else 0.0)


# ---------------------------------------------------------------- helpers purs

def worst_day(daily_pnls):
    """Pire PnL journalier ; 0 si vide (aucune exec n'est 'pas de perte')."""
    return min(daily_pnls) if daily_pnls else 0.0


def constraints_from(daily_pnls, n_tr, n_ab):
    """Valeurs de contrainte Optuna : faisable <=> valeur <= 0."""
    return dict(
        min_trades=10 - n_tr,                              # n_tr >= 10
        abort_rate=n_ab / max(n_tr, 1) - 0.30,             # <= 30 %
        no_losing_day=-worst_day(daily_pnls),              # min_d PnL_d >= 0
    )


def pick_best_row(rows):
    """Lexicographique : (1) faisable = aucun jour perdant + trades + aborts ;
    (2) fallback documente = max(worst_day) puis max(total).

    rows : listes de dicts {value, daily_pnls, constraints{...}}."""
    if not rows:
        return None
    feas = [r for r in rows
            if all(v <= 0 for v in (r.get("constraints") or {}).values())]
    if feas:
        return max(feas, key=lambda r: r["value"])
    return max(rows, key=lambda r: (worst_day(r["daily_pnls"]), r["value"]))


# ---------------------------------------------------------------- objectif

def objective_factory(eng, cands_by_day):
    def objective(trial):
        p = dict(
            fresh_ms=trial.suggest_float("fresh_ms", 100.0, 2000.0, log=True),
            spread_cap=trial.suggest_float("spread_cap", 1.2, 3.0),
            comm_scale=trial.suggest_float("comm_scale", 1.0, 1.5),
            ddd_min=trial.suggest_float("ddd_min", 0.0, 40.0),
            lat_ms=trial.suggest_float("lat_ms", 1.0, 10.0),
            tau_slip_bp=trial.suggest_float("tau_slip_bp", 0.3, 5.0, log=True),
            q_min=trial.suggest_float("q_min", 0.1, 0.95),
            cooldown_s=trial.suggest_float("cooldown_s", 0.0, 120.0),
        )
        daily = []; n_tr = n_ab = 0; total = 0.0
        for step, day in enumerate(TRAIN_DAYS):
            r = replay(eng, cands_by_day[day], p)
            daily.append(r["pnl"]); total += r["pnl"]
            n_tr += r["trades"]; n_ab += r["aborts"]
            trial.report(total, step)
            if trial.should_prune():
                raise optuna.TrialPruned()
        for k, v in constraints_from(daily, n_tr, n_ab).items():
            trial.set_constraint(k, v)
        trial.set_user_attr("trades", n_tr)
        trial.set_user_attr("aborts", n_ab)
        trial.set_user_attr("pnl_total", round(total, 2))
        trial.set_user_attr("daily_pnls", [round(x, 2) for x in daily])
        trial.set_user_attr("worst_day", round(worst_day(daily), 2))
        return total
    return objective


def make_study(name, sampler):
    return optuna.create_study(
        study_name=name, storage=STORAGE, direction="maximize",
        load_if_exists=True, sampler=sampler,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=8, n_warmup_steps=0),
    )


def trial_rows(study):
    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        rows.append(dict(value=t.value,
                         daily_pnls=t.user_attrs.get("daily_pnls") or [],
                         constraints=dict(t.constraints or {})))
    return rows


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    t0 = time.time()

    eng = Engine()
    cands_by_day = load_cands()
    print({d: len(v) for d, v in cands_by_day.items()},
          f"charge en {time.time()-t0:.0f}s", flush=True)

    if mode == "eval":
        p = json.loads(sys.argv[2])
        rep = {d: replay(eng, cands_by_day[d], p) for d in TRAIN_DAYS}
        print(json.dumps(rep, indent=2))
        return

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = make_study("poc_exec_pen_v1",
                       optuna.samplers.TPESampler(seed=SEED, multivariate=True,
                                                constant_liar=True))
    if len(study.trials) == 0:
        study.enqueue_trial(DOC_DEFAULTS)
    n = 10 if mode == "smoke" else int(sys.argv[2]) if len(sys.argv) > 2 else 150
    study.optimize(objective_factory(eng, cands_by_day), n_trials=n, n_jobs=1)

    if mode != "smoke":
        rs = make_study("poc_exec_pen_rand",
                        optuna.samplers.RandomSampler(seed=SEED))
        rs.optimize(objective_factory(eng, cands_by_day),
                    n_trials=max(20, n // 4), n_jobs=1)

    rows = trial_rows(study)
    feas = [r for r in rows
            if all(v <= 0 for v in r["constraints"].values())]
    b = pick_best_row(rows)
    if b is None:
        print("aucun trial complete"); return
    # retrouve le trial correspondant pour les params
    cand = [t for t in study.trials
            if t.state == optuna.trial.TrialState.COMPLETE
            and t.value == b["value"]
            and t.user_attrs.get("daily_pnls") == b["daily_pnls"]]
    bt = cand[0] if cand else study.best_trial
    res = dict(best_pnl=b["value"], params=bt.params,
               daily_pnls=b["daily_pnls"], worst_day=worst_day(b["daily_pnls"]),
               feasible_zero_loss=bool(feas),
               n_feasible=len(feas), n_trials=len(study.trials),
               constraints=dict(bt.constraints or {}),
               elapsed=time.time() - t0)
    (OUT / "best_params_pen.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
