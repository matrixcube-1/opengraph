#!/usr/bin/env python3
"""Tests unitaires de l'extension mixte (sauts métal intérieurs) — sans data.

Vérifie l'arithmétique eval/exec sur un Engine synthétique :
  T1 P_S == p_start pour TOUS les chemins (bug : jambe 0 = saut métal
     laissait P_S=0=USD -> qty0 sur le mauvais noeud -> net fantôme ~+10k)
  T2 prix cohérents -> aucun chemin mixte profitable (net argmax < 0)
  T3 enum_paths(mixed=False) == univers FX seul (2 072), mixed=True > FX
  T4 1 hop métal = 1 jambe logique (P_L) mais 2 ordres physiques
"""
import numpy as np

import poc_engine as pe

CI = pe.CI
NOTIONAL = pe.NOTIONAL


def _mk_engine(syms, quotes):
    """Engine synthétique : pas de CSV — bid/ask/last_t injectés."""
    e = pe.Engine.__new__(pe.Engine)
    e.mixed = True
    e.syms = syms
    e.data = {s: (np.array([0]), np.array([quotes[s][0]]),
                 np.array([quotes[s][1]]), np.array([0.0])) for s in syms}
    e.slist = sorted(e.data)
    e.sidx = {s: i for i, s in enumerate(e.slist)}
    e.med_spread = {s: quotes[s][1] - quotes[s][0] for s in syms}
    e.MED = np.array([e.med_spread[s] for s in e.slist])
    e.IS_FX = np.array([s in set(pe.FX) for s in e.slist])
    e.B_SI = np.array([e.sidx[s] for s in pe.BOUTS if s in e.sidx])
    e.paths, e.p_start = pe.enum_paths(True)
    e.fe = {s: (CI[s[:3]], CI[s[3:]]) for s in pe.FX}
    e._vectorize()
    n = len(e.slist)
    e.bid = np.full(n, np.nan); e.ask = np.full(n, np.nan)
    e.last_t = np.full(n, -1e18)
    e.pos = {s: 0 for s in e.data}
    for s, (b, a) in quotes.items():
        i = e.sidx[s]
        e.bid[i] = b; e.ask[i] = a; e.last_t[i] = 0
    return e


# Prix cohérents : 1 EUR = 1.10 USD, 1 GBP = 1.30 USD, XAU = 2600 USD
# => XAUEUR = 2363.6 EUR, XAUGBP = 2000.0 GBP (implied EURGBP 0.846)
QUOTES = {
    "EURUSD": (1.0999, 1.1001),
    "GBPUSD": (1.2999, 1.3001),
    "XAUUSD": (2599.5, 2600.5),
    "XAUEUR": (2363.0, 2364.3),
    "XAUGBP": (1999.5, 2000.5),
}


def test_p_s_equals_p_start():
    e = _mk_engine(list(QUOTES), QUOTES)
    bad = np.nonzero(e.P_S != e.p_start)[0]
    assert len(bad) == 0, (
        f"{len(bad)} chemins avec P_S != p_start (ex. pi={bad[0]} "
        f"path={e.paths[bad[0]]} p_start={pe.CURR[e.p_start[bad[0]]]} "
        f"P_S={pe.CURR[e.P_S[bad[0]]]})")


def test_consistent_prices_no_profit():
    e = _mk_engine(list(QUOTES), QUOTES)
    ev = e.eval_at(0, fresh_ms=500, spread_cap=2.5)
    assert ev is not None
    net, pi, ddd = ev
    assert net < 0, (
        f"argmax net={net:.2f} >= 0 sur prix coherents -> faux profit "
        f"(pi={pi} path={e.paths[pi]} start={pe.CURR[e.p_start[pi]]})")


def test_enum_paths_fx_only_unchanged():
    paths_fx, _ = pe.enum_paths(False)
    paths_mx, _ = pe.enum_paths(True)
    assert len(paths_fx) == 2072, f"FX-only: {len(paths_fx)} != 2072"
    assert len(paths_mx) > len(paths_fx)
    assert all(not isinstance(e, tuple) or e[0] == "B"
               for p in paths_fx for e in p), "FX-only contient un saut metal"


def test_metal_hop_is_one_leg():
    e = _mk_engine(list(QUOTES), QUOTES)
    hop = ("X", "XAU", CI["GBP"])
    pi = e.paths.index((hop,))
    assert e.P_L[pi] == 1, f"P_L={e.P_L[pi]} != 1 pour 1 hop metal"
    assert e.P_ISM[pi, 0] and not e.P_ISM[pi, 1:].any()
    assert e.p_start[pi] == CI["EUR"] or e.P_S[pi] == CI[pe.CURR[e.p_start[pi]]]


if __name__ == "__main__":
    for f in [test_p_s_equals_p_start, test_consistent_prices_no_profit,
              test_enum_paths_fx_only_unchanged, test_metal_hop_is_one_leg]:
        f()
        print(f"{f.__name__} OK")
