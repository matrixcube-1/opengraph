#!/usr/bin/env python3
"""Tests TDD — helpers purs de poc_optuna_pen (objectif penalise / contrainte
« aucun jour perdant »). Aucune donnee distante requise."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from poc_optuna_pen import constraints_from, pick_best_row, worst_day


def test_worst_day():
    assert worst_day([48393.0, -628.0, -59.0]) == -628.0
    assert worst_day([45655.0, 349.0, 56.0]) == 56.0
    assert worst_day([]) == 0.0


def test_constraints_losing_day_infeasible():
    c = constraints_from([48393.0, -628.0, -59.0], n_tr=100, n_ab=0)
    assert c["no_losing_day"] > 0            # min_d = -628 -> -min_d = +628 > 0
    assert c["min_trades"] <= 0
    assert c["abort_rate"] <= 0


def test_constraints_all_positive_feasible():
    c = constraints_from([45655.0, 349.0, 56.0], n_tr=1000, n_ab=4)
    assert all(v <= 0 for v in c.values())


def test_constraints_abort_rate_blocks():
    c = constraints_from([10.0, 10.0, 10.0], n_tr=100, n_ab=50)
    assert c["abort_rate"] > 0             # 50% > 30%


def test_pick_best_prefers_zero_loss_day():
    rows = [
        dict(value=47706.0, daily_pnls=[48393.0, -628.0, -59.0],
             constraints=dict(min_trades=-100.0, abort_rate=-0.29,
                              no_losing_day=628.0)),
        dict(value=46060.0, daily_pnls=[45655.0, 349.0, 56.0],
             constraints=dict(min_trades=-1000.0, abort_rate=-0.30,
                              no_losing_day=-56.0)),
    ]
    best = pick_best_row(rows)
    assert best["value"] == 46060.0        # zero jour perdant gagne le lexico


def test_pick_best_fallback_worst_day():
    """Aucun trial realisable -> meilleur compromis : max(worst_day), puis total."""
    rows = [
        dict(value=48000.0, daily_pnls=[49000.0, -900.0, -100.0],
             constraints=dict(no_losing_day=900.0)),
        dict(value=30000.0, daily_pnls=[31000.0, -900.0, -100.0],
             constraints=dict(no_losing_day=900.0)),
        dict(value=20000.0, daily_pnls=[21000.0, -500.0, -500.0],
             constraints=dict(no_losing_day=500.0)),
    ]
    best = pick_best_row(rows)
    assert best["value"] == 20000.0        # worst_day -500 > -900 gagne


def test_pick_best_none():
    assert pick_best_row([]) is None


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__} {e}")
    sys.exit(1 if failed else 0)
