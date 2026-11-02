#!/usr/bin/env python3
"""Tests de régression audit OG2 (matrixcube-1, nov. 2026) — côté Python.

  B4 : EPOCH = minuit UTC exact — insensible au fuseau local et au DST
       (l'ancienne formule mktime-timezone décalait les jours CEST d'1 h
       sur machine non-UTC).
  D2 : _abort = coût par arête (med_rel x notional + comm), pas 12 $ fixe.
  B1 : on_tick avec edge_id >= 28 -> None, pas de panic Rust.
"""
import numpy as np

import poc_engine as pe


def test_epoch_is_pure_utc():
    # audit OG2-B4 : ces valeurs sont le minuit UTC exact (epoch_ms).
    assert pe.EPOCH["2026-10-22"] == 1_792_627_200_000
    assert pe.EPOCH["2026-10-23"] - pe.EPOCH["2026-10-22"] == 86_400_000
    assert pe.EPOCH["2026-10-26"] - pe.EPOCH["2026-10-23"] == 3 * 86_400_000


def _mk_abort_engine(quotes):
    """Engine synthétique minimal pour _abort (bid/ask injectés)."""
    e = pe.Engine.__new__(pe.Engine)
    e.slist = sorted(quotes)
    e.sidx = {s: i for i, s in enumerate(e.slist)}
    n = len(e.slist)
    e.bid = np.full(n, np.nan); e.ask = np.full(n, np.nan)
    e.med_spread = {}
    for s, (b, a) in quotes.items():
        i = e.sidx[s]
        e.bid[i], e.ask[i] = b, a
        e.med_spread[s] = a - b
    e.MED = np.array([e.med_spread[s] for s in e.slist])
    return e


def test_abort_cost_scales_with_spread():
    # audit OG2-D2 : une paire à spread 2 bp coûte ~23 $ à déboucler,
    # pas 12 $ (le proxy fixe sous-estimait ~2x les paires larges).
    e = _mk_abort_engine({
        "EURUSD": (1.0999, 1.1001),       # ~1.8 bp
        "CADJPY": (110.60, 110.622),      # ~2 bp -> ~23 $/jambe
    })
    ei = pe.FX.index("CADJPY")
    sj = e.sidx["CADJPY"]
    r = e._abort("x", [ei], 1, 0.0, 0.0, 0.0)
    mid = 0.5 * (e.bid[sj] + e.ask[sj])
    exp = -(e.MED[sj] / mid * pe.NOTIONAL + pe.COMM)
    assert abs(r["pnl"] - exp) < 1e-9, f"pnl={r['pnl']} attendu {exp}"
    assert abs(exp + 23.0) < 0.5, "spread ~2 bp -> ~23 $, pas 12 $"
    # 2 jambes remplies : coût = somme par arête
    ei2 = pe.FX.index("EURUSD")
    r2 = e._abort("x", [ei, ei2], 2, 0.0, 0.0, 0.0)
    sj2 = e.sidx["EURUSD"]
    mid2 = 0.5 * (e.bid[sj2] + e.ask[sj2])
    exp2 = exp - (e.MED[sj2] / mid2 * pe.NOTIONAL + pe.COMM)
    assert abs(r2["pnl"] - exp2) < 1e-9


def test_abort_cost_fallback_when_no_quote():
    # arête sans mid valide -> repli sur la constante 12 $ (comportement sûr)
    e = _mk_abort_engine({"EURUSD": (np.nan, np.nan)})
    ei = pe.FX.index("EURUSD")
    r = e._abort("x", [ei], 1, 0.0, 0.0, 0.0)
    assert r["pnl"] == -12.0


def test_abort_ignores_metal_leg_for_spread_cost():
    # jambe métal (tuple) : pas de med_rel FX -> repli constante par jambe
    e = _mk_abort_engine({"EURUSD": (1.0999, 1.1001)})
    leg_metal = ("X", "XAU", pe.CI["EUR"])
    r = e._abort("x", [leg_metal], 1, 0.0, 0.0, 0.0)
    assert r["pnl"] == -12.0


def test_on_tick_out_of_range_edge_returns_none():
    # audit OG2-B1 : e >= 28 -> None ; avant : panic hors borne fx_si[28].
    import opengraph
    eng = opengraph.Engine.live()
    assert eng.on_tick(28, 1000, 1.0, 1.0, 1.0, 500.0, 2.5, 1.0) is None
    assert eng.on_tick(255, 1000, 1.0, 1.0, 1.0, 500.0, 2.5, 1.0) is None


if __name__ == "__main__":
    for f in [test_epoch_is_pure_utc, test_abort_cost_scales_with_spread,
              test_abort_cost_fallback_when_no_quote,
              test_abort_ignores_metal_leg_for_spread_cost,
              test_on_tick_out_of_range_edge_returns_none]:
        f()
        print(f"{f.__name__} OK")
