#!/usr/bin/env python3
"""POC — driver : scan des candidats (1 passe) + Optuna v5 (inventaire enrichi §15-18).

Usage :
  python3 poc_optuna.py scan        # passe lourde -> cand.npz (sur-ensemble détection)
  python3 poc_optuna.py smoke       # 10 trials TPE + baseline doc
  python3 poc_optuna.py run 200     # campagne (reprise via sqlite)
  python3 poc_optuna.py holdout     # évalue le best sur le jour hold-out (UNE fois)
  python3 poc_optuna.py importance  # PedAnova

Design (checklist anti-overfitting §16) :
- split temporel : jours 22+23 = in-sample (steps de pruning), jour 26 = HOLD-OUT
- contraintes hard : min trades, abort_rate (set_constraint <=0 = faisable)
- baseline : enqueue_trial défauts du document ; study Random de comparaison
- seed fixe + n_jobs=1 -> déterministe ; user_attrs complets ; SQLite résilient
- Hawkes MLE : hors Optuna (épisodes exportés dans cand.npz)
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import optuna

sys.path.insert(0, str(Path(__file__).parent))
from poc_engine import Engine, DAYS, EPOCH, FX, METAL_SYM

OUT = Path("/opt/arb/poc")
STORAGE = f"sqlite:///{OUT}/optuna.db"

# --mixed : calibration sur l'univers FX + sauts métal (cand_mixed.npz,
# study dédiée — ne contamine pas la study FX-only). Le moteur est restreint
# aux symboles utilisés par les chemins (sync ne marche pas les indices CFD).
def _flag(name):
    """--flag value -> value (retire de argv), ou None."""
    if name in sys.argv:
        i = sys.argv.index(name)
        v = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return v
    return None


MIXED = "--mixed" in sys.argv
if MIXED:
    sys.argv.remove("--mixed")
_cand = _flag("--cand")
_study = _flag("--study")
CAND_FILE = OUT / (_cand or ("cand_mixed.npz" if MIXED else "cand.npz"))
STUDY_NAME = _study or ("poc_exec_mixed" if MIXED else "poc_exec_v2")
RAND_NAME = STUDY_NAME + "_rand" if _study else \
    ("poc_exec_rand_mixed" if MIXED else "poc_exec_rand_v2")
_suffix = _study or ("mixed" if MIXED else None)
BEST_OUT = OUT / (f"best_params_{_suffix}.json" if _suffix else "best_params.json")
HOLDOUT_OUT = OUT / (f"holdout_{_suffix}.json" if _suffix else "holdout.json")


def make_engine():
    if MIXED:
        syms = FX + [ms for cs in METAL_SYM.values() for ms in cs.values()]
        return Engine(syms=syms, mixed=True)
    return Engine(syms=FX)   # bouts non enumeres -> FX seul suffit (sync allegee)
TRAIN_DAYS = DAYS[:2]           # 22, 23 oct. -> in-sample (2 steps de pruning)
HOLDOUT_DAY = DAYS[2]           # 26 oct. -> évalué UNE fois
SEED = 20261027

# défauts du document (baseline enqueue)
DOC_DEFAULTS = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
                    lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)
# bornes du scan générateur = max des plages de tuning
SCAN_GENEROUS = dict(fresh_ms=2000, spread_cap=3.0, thresh=-50.0)


def day_of(t_ms):
    for d, e0 in EPOCH.items():
        if e0 <= t_ms < e0 + 86_400_000:
            return d
    return None


def load_cands():
    z = np.load(CAND_FILE)
    cands = [tuple(c) for c in z["cands"]]
    # garantie bloquante : les candidats doivent etre chronologiques —
    # le « trie par construction » devient un echec, pas un avertissement
    ts = np.asarray([c[0] for c in cands])
    assert np.all(np.diff(ts) >= 0), f"{CAND_FILE} : timestamps non croissants"
    eps = z["episodes"] if "episodes" in z.files else np.empty((0, 4))
    by_day = {d: [] for d in DAYS}
    for c in cands:
        d = day_of(int(c[0]))
        if d:
            by_day[d].append(c)
    return by_day, eps


def replay(eng, cands, p):
    """Exécute E1-E9 sur les candidats du jour ; détection ré-évaluée sous les
    paramètres du trial (fresh/cap/comm filtrent le sur-ensemble du scan)."""
    pnl = 0.0; n = 0; aborts = 0; wins = 0; durs = []
    t_free = -1e18
    for t, _pi0, _net0, _ddd0 in cands:
        if t < t_free:
            continue
        # borne prouvee : net_stocké = argmax sous bornes généreuses du scan
        # (fresh<=2000, cap<=3.0, comm_scale=1) >= tout net ré-évalué sous les
        # bornes plus serrées du trial -> si déjà < ddd_min, l'éval est vaine
        if _net0 < p["ddd_min"]:
            continue
        eng.sync(int(t))
        ev = eng.eval_at(int(t), p["fresh_ms"], p["spread_cap"], p["comm_scale"],
                         rows=eng.IDX_FX)
        if ev is not None and ev[0] >= p["ddd_min"]:
            # Convention A : les terminaisons bouts sont mecaniquement
            # dominees — la 2e eval sur tout l'univers ne doit JAMAIS gagner.
            # Si elle gagne un jour, la convention est remise en cause.
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


def objective_factory(eng, cands_by_day):
    def objective(trial):
        p = dict(
            fresh_ms=trial.suggest_float("fresh_ms", 100.0, 2000.0, log=True),
            spread_cap=trial.suggest_float("spread_cap", 1.2, 3.0),
            comm_scale=trial.suggest_float("comm_scale", 1.0, 1.5),   # jamais < taux negocie
            ddd_min=trial.suggest_float("ddd_min", 0.0, 40.0),
            lat_ms=trial.suggest_float("lat_ms", 1.0, 10.0),
            tau_slip_bp=trial.suggest_float("tau_slip_bp", 0.3, 5.0, log=True),
            q_min=trial.suggest_float("q_min", 0.1, 0.95),
            cooldown_s=trial.suggest_float("cooldown_s", 0.0, 120.0),
        )
        total = 0.0; n_tr = n_ab = 0
        for step, day in enumerate(TRAIN_DAYS):
            r = replay(eng, cands_by_day[day], p)
            total += r["pnl"]; n_tr += r["trades"]; n_ab += r["aborts"]
            trial.report(total, step)               # P&L cumulé (comparable entre trials)
            if trial.should_prune():
                raise optuna.TrialPruned()
        trial.set_constraint("min_trades", 10 - n_tr)           # n_tr >= 10
        trial.set_constraint("abort_rate",
                             n_ab / max(n_tr, 1) - 0.30)        # <= 30 %
        trial.set_user_attr("trades", n_tr)
        trial.set_user_attr("aborts", n_ab)
        trial.set_user_attr("pnl_total", round(total, 2))
        return total
    return objective


def make_study(name, sampler):
    return optuna.create_study(
        study_name=name, storage=STORAGE, direction="maximize",
        load_if_exists=True, sampler=sampler,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=8, n_warmup_steps=0),
    )


def feasible_trials(study):
    out = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        cons = t.constraints
        vals = list(cons.values()) if isinstance(cons, dict) else list(cons or [])
        if all(v <= 0 for v in vals):
            out.append(t)
    return out


def pick_best(study):
    """best_trial tolérant : faisables d'abord ; sinon meilleur COMPLETE brut
    (v5 lève ValueError si aucun faisable — le cas sous-univers sans opportunité)."""
    feas = feasible_trials(study)
    pool = feas or [t for t in study.trials
                    if t.state == optuna.trial.TrialState.COMPLETE]
    return max(pool, key=lambda t: t.value) if pool else None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    t0 = time.time()

    if mode == "scan":
        eng = make_engine()
        cands, eps = eng.scan(out=CAND_FILE, **SCAN_GENEROUS)
        print(f"scan {time.time()-t0:.0f}s -> {len(cands):,} candidats, {len(eps):,} épisodes")
        if len(eps):
            d = (np.array([e[1]-e[0] for e in eps])) / 1000.0
            print("durées épisodes (s): méd=%.3f p90=%.3f max=%.1f" %
                  (np.median(d), np.quantile(d, 0.9), d.max()))
        return

    eng = make_engine()
    cands_by_day, eps = load_cands()
    print({d: len(v) for d, v in cands_by_day.items()}, f"chargé en {time.time()-t0:.0f}s")

    if mode == "holdout":
        study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
        best = pick_best(study)
        if best is None:
            print("aucun trial complété"); return
        r = replay(eng, cands_by_day[HOLDOUT_DAY], best.params)
        res = dict(best_params=best.params, in_sample=best.value,
                   feasible=all(v <= 0 for v in (best.constraints or {}).values()),
                   holdout_day=HOLDOUT_DAY, holdout=r)
        HOLDOUT_OUT.write_text(json.dumps(res, indent=2))
        print(json.dumps(res, indent=2))
        return

    if mode == "importance":
        from optuna.importance import get_param_importances
        study = optuna.load_study(study_name=STUDY_NAME, storage=STORAGE)
        try:
            # PedAnova vise les FAIBLES valeurs -> maximize exige target=-value (doc §9)
            print(get_param_importances(study, target=lambda t: -t.value))
        except Exception as e:
            print(f"importance indisponible: {e}")
        return

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = make_study(STUDY_NAME,
                       optuna.samplers.TPESampler(seed=SEED, multivariate=True,
                                                constant_liar=True))
    if len(study.trials) == 0:
        study.enqueue_trial(DOC_DEFAULTS)           # baseline document (cap 1.7)
    n = 10 if mode == "smoke" else int(sys.argv[2]) if len(sys.argv) > 2 else 200
    study.optimize(objective_factory(eng, cands_by_day), n_trials=n, n_jobs=1)

    # study de comparaison Random (checklist #5 — mesure le gain du sampler)
    if mode != "smoke":
        rs = make_study(RAND_NAME, optuna.samplers.RandomSampler(seed=SEED))
        rs.optimize(objective_factory(eng, cands_by_day), n_trials=max(20, n // 4),
                    n_jobs=1)

    b = pick_best(study)
    if b is None:
        print("aucun trial complété"); return
    res = dict(best_pnl=b.value, params=b.params,
               attrs={k: b.user_attrs.get(k) for k in ("trades", "aborts", "pnl_total")},
               constraints=b.constraints, n_trials=len(study.trials),
               elapsed=time.time() - t0)
    BEST_OUT.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
