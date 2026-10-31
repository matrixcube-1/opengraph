#!/usr/bin/env python3
"""Diagnostic de câblage §8.6 — flux T̃/D/c⋆ sur les jours PoC.

Réplique NumPy fidèle des formules du document de conception
(tension multi-sauts + dominance SVD) — test de cohérence du câblage,
PAS une preuve statistique. L'artefact de production est la crate Rust
`regime_indicators` (43 tests unitaires).

Grille boucle lente : 1 pas / seconde (temps événementiel).
s̄_ij : médiane journalière du demi-spread relatif (approx. de la médiane
glissante — suffisante pour le contrôle de câblage).

Usage : python3 regime_diag.py [--day YYYY-MM-DD]
Sortie : résumé par jour + regime_stream.npz
"""
import sys, glob, os, json, collections
import numpy as np

DATA = "/opt/arb/data"
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
# Ordre canonique : EUR GBP JPY USD AUD CAD CHF NZD
CCY = ["EUR", "GBP", "JPY", "USD", "AUD", "CAD", "CHF", "NZD"]
IDX = {c: i for i, c in enumerate(CCY)}
N = 8
GRID_MS = 1_000           # 1 s
H_T_NS = 60_000           # demi-vie EWMA tension : 60 s (unité ms ici)
W_T = 600                 # historique z : 600 pts lissés (10 min)
W_MIN = 60                # warm-up z
M_MIN = 1e-9
W_RET = 300               # fenêtre rendements SVD : 300 s (5 min)
L_HOPS = 3
EPS = 1e-12
Q_MIN = 0.5               # seuil de charge u1² pour c⋆


def load_day(day):
    """Charge les 28 paires -> dict sym -> (ms, mid, half_spread_rel)."""
    out = {}
    for sym, (b, q) in SYMBOLS.items():
        ms_list, mid_list, hs_list = [], [], []
        for f in sorted(glob.glob(f"{DATA}/{sym}/{day}/[0-9][0-9].csv")):
            hh_ms = int(os.path.basename(f)[:2]) * 3_600_000
            a = np.loadtxt(f, delimiter=",", skiprows=1)
            if a.size == 0:
                continue
            a = np.atleast_2d(a)
            mid = 0.5 * (a[:, 1] + a[:, 2])
            hs = 0.5 * (a[:, 1] - a[:, 2]) / np.maximum(mid, 1e-12)
            ms_list.append(a[:, 0].astype(np.int64) + hh_ms)
            mid_list.append(mid)
            hs_list.append(hs)
        if ms_list:
            out[sym] = (np.concatenate(ms_list), np.concatenate(mid_list),
                        np.concatenate(hs_list))
    return out


def build_pairs(day):
    """ms -> matrices x (log mid) et sbar par sym."""
    raw = load_day(day)
    pairs = []
    for sym, (ms, mid, hs) in raw.items():
        b, q = SYMBOLS[sym]
        pairs.append(dict(sym=sym, bi=IDX[b], qi=IDX[q],
                          ms=ms, mid=np.log(mid), hs=hs))
    return pairs


def step_state(pairs, t):
    """Forward-fill : log-mid par paire à l'instant t. None si trou."""
    x = np.zeros((N, N))
    got = np.zeros((N, N), dtype=bool)
    for p in pairs:
        i = np.searchsorted(p["ms"], t, side="right") - 1
        if i < 0:
            return None, False
        v = p["mid"][i]
        x[p["bi"], p["qi"]] = v
        x[p["qi"], p["bi"]] = -v
        got[p["bi"], p["qi"]] = got[p["qi"], p["bi"]] = True
    return x, True


def tension_chain(x, sbar):
    """p̂ → R → A → Ā → Ã → M → T. K8 : p̂_i = moyenne ligne."""
    p = x.sum(axis=1) / N          # Prop 1(i) : graphe complet
    R = x - (p[:, None] - p[None, :])
    np.fill_diagonal(R, 0.0)
    A = np.abs(R) / sbar
    np.fill_diagonal(A, 0.0)
    d = A.sum(axis=1)
    inv = np.where(d > EPS, 1.0 / np.sqrt(np.maximum(d, EPS)), 0.0)
    Abar = inv[:, None] * A * inv[None, :]
    A2 = Abar @ Abar
    A3 = A2 @ Abar
    At = Abar + A2 + A3            # L = 3
    sq = np.where(d > EPS, np.sqrt(np.maximum(d, EPS)), 0.0)
    M = sq[:, None] * At * sq[None, :]
    T = np.linalg.norm(M) / (L_HOPS * N)
    return T, p


def main():
    only = None
    if "--day" in sys.argv:
        only = sys.argv[sys.argv.index("--day") + 1]
    days = [only] if only else DAYS
    all_out = {}
    for day in days:
        pairs = build_pairs(day)
        if len(pairs) != 28:
            print(f"{day}: {len(pairs)} paires — ignoré"); continue
        # demi-spread normal par paire : médiane journalière
        sbar = np.ones((N, N))
        for p in pairs:
            v = np.median(p["hs"])
            sbar[p["bi"], p["qi"]] = sbar[p["qi"], p["bi"]] = v
        # grille : du premier instant où les 28 paires ont un tick → fin jour
        t0 = max(int(p["ms"][0]) for p in pairs) + 1
        t1 = min(int(p["ms"][-1]) for p in pairs)
        ts = np.arange(t0, t1 + 1, GRID_MS)
        # état boucle lente
        ewma = None; hist = collections.deque(maxlen=W_T)
        gram_y = collections.deque(maxlen=W_RET)
        prev_p = None
        T_raw = np.full(len(ts), np.nan)
        T_z = np.full(len(ts), np.nan)
        D = np.full(len(ts), np.nan)
        Cstar = np.full(len(ts), -1)
        dirs = np.zeros(len(ts), dtype=int)
        for k, t in enumerate(ts):
            x, ok = step_state(pairs, t)
            if not ok:
                continue
            tr, p = tension_chain(x, sbar)
            T_raw[k] = tr
            # EWMA demi-vie
            if ewma is None:
                ewma, last_t = tr, t
            else:
                a = 1 - np.exp(-np.log(2) * (t - last_t) / H_T_NS)
                ewma += a * (tr - ewma); last_t = t
            # z robuste : contre hist AVANT push (pas de fuite)
            if len(hist) >= W_MIN:
                h = np.array(hist)
                med = np.median(h)
                mad = np.median(np.abs(h - med))
                T_z[k] = (ewma - med) / (1.4826 * max(mad, M_MIN))
            hist.append(ewma)
            # dominance
            if prev_p is not None:
                y = p - prev_p
                gram_y.append(y)
                if len(gram_y) == W_RET:
                    Y = np.array(gram_y).T          # 8 x W
                    Yc = Y - Y.mean(axis=1, keepdims=True)
                    sv = np.linalg.svd(Yc, compute_uv=False)
                    e = (sv ** 2).sum()
                    if e > 0:
                        D[k] = sv[0] ** 2 / e
                        # c⋆ : charge max de u1 (SVD avec vecteurs, rare)
                        u = np.linalg.svd(Yc)[0][:, 0]
                        c = int(np.argmax(u ** 2))
                        if u[c] ** 2 >= Q_MIN:
                            Cstar[k] = c
                            dirs[k] = 1 if Y[c].sum() >= 0 else -1
            prev_p = p
        all_out[day] = dict(ts=ts, T_raw=T_raw, T_z=T_z, D=D,
                            cstar=Cstar, dir=dirs)
        # résumé
        def q(a):
            a = a[np.isfinite(a)]
            return dict(n=len(a), med=float(np.median(a)),
                        p90=float(np.quantile(a, 0.9)),
                        p99=float(np.quantile(a, 0.99)),
                        max=float(a.max())) if len(a) else {}
        cstat = collections.Counter(Cstar[Cstar >= 0].tolist())
        cnames = {i: CCY[i] for i in range(8)}
        print(f"=== {day} ===")
        print(f"  T_raw : {q(T_raw)}")
        print(f"  T_z   : {q(T_z)}")
        print(f"  D     : {q(D)}")
        print(f"  c⋆    : {sorted(((cnames[i], n) for i, n in cstat.items()), key=lambda x: -x[1])[:4]}"
              f"  (couverture {100*(Cstar>=0).mean():.0f}%)")
    np.savez_compressed("/opt/arb/poc/regime_stream.npz", **all_out)
    print("sauvegardé /opt/arb/poc/regime_stream.npz")


# mapping symbole -> (base, quote)
SYMBOLS = {s: (s[:3], s[3:]) for s in [
    "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD",
    "CADCHF", "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "EURUSD",
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD",
    "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY",
]}

if __name__ == "__main__":
    main()
