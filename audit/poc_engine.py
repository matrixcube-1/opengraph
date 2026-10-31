#!/usr/bin/env python3
"""POC — replay événementiel de ticks réels (Annexe J) + exécution E1-E9 + Optuna.

  Engine       : charge les CSV ; boucle événementielle incrémentale O(1)/event
  eval_at(t)   : meilleur chemin ouvert par DDD net (vectorisé, conv. A exécutable)
  exec_seq     : séquence IOC par jambe, fill au dernier tick <= t_fill (E1-E9)
  scan         : une passe -> instants candidats + épisodes d'opportunité (Hawkes)
  runner       : re-notation des candidats + exec sim -> P&L réalisable (par trial)

Conventions du document :
- chemins ouverts <=3 jambes devises (K8) + terminaison bout optionnelle (puits)
- DDD aux taux exécutables (bid/ask réels) ; résiduel FX marqué mid, bout marqué au bid
- coûts : commission 3.50/up[base] par jambe FX (compte USD) + commission bout
- SWI20 : Duka cote CHF, IC cote USD -> conversion CHF->USD ; *CMDUSX : x0.01
"""
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

DATA = Path("/opt/arb/data")
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]
EPOCH = {d: int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000) - time.timezone * 1000 for d in DAYS}

FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP","EURJPY","EURCHF",
      "EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF",
      "AUDCAD","AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"]
CURR = ["USD","EUR","GBP","JPY","CHF","CAD","AUD","NZD"]
CI = {c: i for i, c in enumerate(CURR)}
BOUTS = {  # duka -> devise de cotation IC (nœud du graphe)
    "DEUIDXEUR":"EUR","ITAIDXEUR":"EUR","FRAIDXEUR":"EUR","EUSIDXEUR":"EUR","NLDIDXEUR":"EUR",
    "XAUEUR":"EUR","XAGEUR":"EUR","GBRIDXGBP":"GBP","XAUGBP":"GBP","JPNIDXJPY":"JPY",
    "XAUJPY":"JPY","XAUCHF":"CHF","AUSIDXAUD":"AUD","XAUAUD":"AUD","XAGAUD":"AUD",
    "USA30IDXUSD":"USD","USA500IDXUSD":"USD","USSC2000IDXUSD":"USD","USATECHIDXUSD":"USD",
    "XAUUSD":"USD","XAGUSD":"USD","LIGHTCMDUSD":"USD","BRENTCMDUSD":"USD",
    "XPTCMDUSD":"USD","XPDCMDUSD":"USD","SUGARCMDUSD":"USD","COCOACMDUSD":"USD",
    "CHEIDXCHF":"USD","COFFEECMDUSX":"USD","COTTONCMDUSX":"USD","SOYBEANCMDUSX":"USD",
    "OJUICECMDUSX":"USD",
}
PRICE_CONV = {s: ("USD", 0.01) for s in
              ("COFFEECMDUSX","COTTONCMDUSX","SOYBEANCMDUSX","OJUICECMDUSX")}
PRICE_CONV["CHEIDXCHF"] = ("CHF", 1.0)
SYMS = FX + list(BOUTS)
NOTIONAL = 100_000.0
COMM = 3.00          # cTrader/negocie volume : 3 $/cote/100k (canonique doc : 3,50)
METAL_COMM = {"XAUUSD":0.875,"XAGUSD":7.292,"XPTCMDUSD":2.333,"XPDCMDUSD":2.692,
              "XAUEUR":0.875,"XAGEUR":7.292,"XAUGBP":0.875,"XAUJPY":0.875,"XAUCHF":0.875,
              "XAUAUD":0.875,"XAGAUD":7.292}
CONV_COST = {"CHEIDXCHF": 4.0}   # jambe USDCHF implicite (comm + demi-spread)

# Sauts métal INTÉRIEURS : XAU coté en 6 devises, XAG en 3 -> le métal
# est un nœud de passage réel du graphe (croisement métal-FX).
# Un hop A->B = 2 ordres séquentiels (achat Metal-A à l'ask, vente
# Metal-B au bid) mais compte pour 1 JAMBE du budget <=3 (décision
# utilisateur). Résiduel toujours en devise -> Convention A inchangée.
METALS = ["XAU", "XAG"]
METAL_CCYS = {"XAU": ("USD","EUR","GBP","JPY","CHF","AUD"),
              "XAG": ("USD","EUR","AUD")}
METAL_SYM = {m: {c: m + c for c in cs} for m, cs in METAL_CCYS.items()}
INCLUDE_TERMINAL_BOUTS = False   # résiduel tenu dominé : 0/1 029 811 argmax


def load_sym(sym):
    ts, bs, as_, vs = [], [], [], []
    for d in DAYS:
        for h in range(24):
            f = DATA / sym / d / f"{h:02d}.csv"
            if not f.exists():
                continue
            t0 = EPOCH[d] + h * 3_600_000
            with open(f) as fh:
                r = csv.reader(fh); next(r)
                for ms, a, b, av, bv in r:
                    ts.append(t0 + int(ms)); as_.append(float(a)); bs.append(float(b))
                    vs.append(max(float(av), float(bv)))
    if not ts:
        return None
    o = np.argsort(ts, kind="stable")
    return (np.asarray(ts)[o], np.asarray(bs)[o], np.asarray(as_)[o], np.asarray(vs)[o])


def enum_paths(mixed=False):
    """Chemins ouverts <=3 jambes : arêtes FX + sauts métal (si mixed).
    Un saut ("X", metal, cible) = 1 jambe logique, 2 ordres réels.
    Retourne (paths, starts) : starts[i] = noeud de depart reel du DFS —
    un chemin peut partir du cote quote de sa 1ere arete."""
    fe = [(CI[s[:3]], CI[s[3:]], s) for s in FX]
    adj = {i: [] for i in range(8)}
    for ei, (a, b, s) in enumerate(fe):
        adj[a].append((ei, b))
        adj[b].append((ei, a))          # paire bidirectionnelle
    # adjacence métal : métal coté dans les deux devises -> hop possible
    madj = {i: [] for i in range(8)}
    if mixed:
        for m in METALS:
            cs = METAL_CCYS[m]
            for i, c1 in enumerate(cs):
                for c2 in cs[i + 1:]:
                    a, b = CI[c1], CI[c2]
                    madj[a].append((m, b))
                    madj[b].append((m, a))
    paths = []; starts = []
    def dfs(node, seen, legs, eis, s0):
        if legs:
            paths.append(tuple(eis)); starts.append(s0)
            if INCLUDE_TERMINAL_BOUTS:
                for bs, qc in BOUTS.items():
                    if CI[qc] == node:
                        paths.append(tuple(eis) + (("B", bs),)); starts.append(s0)
        if legs >= 3:
            return
        for ei, b in adj[node]:
            if b not in seen:
                dfs(b, seen | {b}, legs + 1, eis + [ei], s0)
        for m, b in madj[node]:
            if b not in seen:
                dfs(b, seen | {b}, legs + 1, eis + [("X", m, b)], s0)
    for s0 in range(8):
        dfs(s0, {s0}, 0, [], s0)
    return paths, starts


class Engine:
    def __init__(self, syms=None, verbose=True, mixed=False):
        self.mixed = mixed
        self.syms = syms or SYMS
        self.data = {s: load_sym(s) for s in self.syms}
        self.data = {s: v for s, v in self.data.items() if v is not None}
        self.slist = sorted(self.data)
        self.sidx = {s: i for i, s in enumerate(self.slist)}
        self.med_spread = {s: float(np.median(v[2] - v[1])) for s, v in self.data.items()}
        self.MED = np.array([self.med_spread[s] for s in self.slist])
        self.IS_FX = np.array([s in set(FX) for s in self.slist])
        # conversion Duka -> nœud IC : le facteur s'annule dans bid/ask — seul
        # résidu : coût de la conversion SWI20 CHF->USD (~1 jambe FX) et USX (x0.01)
        self.B_SI = np.array([self.sidx[s] for s in BOUTS if s in self.sidx])
        self.paths, self.p_start = enum_paths(mixed)
        self.fe = {s: (CI[s[:3]], CI[s[3:]]) for s in FX}
        self._vectorize()
        # événements de détection : FX + dénominations métal (un décrochage
        # métal sans mouvement FX doit aussi déclencher l'évaluation)
        ev_t, ev_i = [], []
        ev_syms = FX + ([ms for cs in METAL_SYM.values() for ms in cs.values()]
                        if self.mixed else [])
        for s in ev_syms:
            if s in self.sidx:
                t = self.data[s][0]
                ev_t.append(t); ev_i.append(np.full(len(t), self.sidx[s]))
        self.ev_t = np.concatenate(ev_t); self.ev_i = np.concatenate(ev_i)
        o = np.argsort(self.ev_t, kind="stable")
        self.ev_t, self.ev_i = self.ev_t[o], self.ev_i[o]
        # état incrémental
        self.pos = {s: 0 for s in self.data}
        n = len(self.slist)
        self.bid = np.full(n, np.nan); self.ask = np.full(n, np.nan)
        self.last_t = np.full(n, -1e18)
        if verbose:
            tot = sum(len(v[0]) for v in self.data.values())
            n_mixed = int(self.P_ISM.any(1).sum())
            print(f"{len(self.data)}/{len(self.syms)} symboles | {tot:,} ticks | "
                  f"{len(self.paths)} chemins ({len(self.paths)-n_mixed} FX + {n_mixed} mixtes) | "
                  f"{len(self.ev_t):,} events FX")

    def _vectorize(self):
        """Précompilation des chemins en matrices numpy."""
        n_p = len(self.paths)
        self.P_E = np.full((n_p, 3), -1)          # indice d'arête FX (0..27) par jambe
        self.P_DIR = np.ones((n_p, 3))            # +1 vente base / -1 achat base
        self.P_BASE = np.full((n_p, 3), -1)       # idx devise de base (comm)
        self.E_S = np.full(len(FX), -1)           # arête -> slist idx (-1 si non chargé)
        for ei, s in enumerate(FX):
            self.E_S[ei] = self.sidx.get(s, -1)
        self.P_S = np.zeros(n_p, int)
        self.P_END = np.zeros(n_p, int)
        self.P_HASB = np.zeros(n_p, bool)         # terminaison bout structurelle
        self.P_BOUT = np.full(n_p, -1)            # slist idx du bout (-1 = non chargé)
        self.P_L = np.zeros(n_p, int)
        self.P_CB = np.zeros(n_p)                  # commission non-FX par chemin
        self.P_ISM = np.zeros((n_p, 3), bool)     # jambe = saut métal
        self.P_M1 = np.full((n_p, 3), -1)         # slist idx sym métal départ
        self.P_M2 = np.full((n_p, 3), -1)         # slist idx sym métal arrivée
        for pi, path in enumerate(self.paths):
            node = self.p_start[pi]; li = 0
            self.P_S[pi] = node          # noeud de depart (jambe 0 peut etre metal)
            for e in path:
                if isinstance(e, tuple):
                    if e[0] == "B":
                        self.P_HASB[pi] = True
                        self.P_BOUT[pi] = self.sidx.get(e[1], -1)
                        self.P_CB[pi] += METAL_COMM.get(e[1], 0.0) + CONV_COST.get(e[1], 0.0)
                        continue
                    # ("X", métal, cible) : saut intérieur — 2 ordres, 1 jambe
                    m, to = e[1], e[2]
                    s1 = METAL_SYM[m][CURR[node]]
                    s2 = METAL_SYM[m][CURR[to]]
                    self.P_ISM[pi, li] = True
                    self.P_M1[pi, li] = self.sidx.get(s1, -1)
                    self.P_M2[pi, li] = self.sidx.get(s2, -1)
                    self.P_CB[pi] += (METAL_COMM.get(s1, 0.0)
                                      + METAL_COMM.get(s2, 0.0))
                    node = to
                    li += 1
                    self.P_END[pi] = node
                    continue
                s = FX[e]; a, b = CI[s[:3]], CI[s[3:]]
                d = +1 if node == a else -1
                self.P_DIR[pi, li] = d
                self.P_E[pi, li] = e
                self.P_BASE[pi, li] = a
                node = b if d == +1 else a
                li += 1
                self.P_END[pi] = node
            self.P_L[pi] = li
        self.IDX_FX = np.nonzero(~self.P_HASB)[0]
        self.IDX_BT = np.nonzero(self.P_HASB)[0]

    def _advance(self, sym, t):
        ts = self.data[sym][0]
        j = self.pos[sym]
        if j < 0 or ts[j] > t:                           # rembobinage (replay inter-trials)
            j = int(np.searchsorted(ts, t, "right")) - 1
        else:
            while j + 1 < len(ts) and ts[j + 1] <= t:
                j += 1
        self.pos[sym] = j
        return j

    def tick(self, sym, t):
        """Met à jour l'état avec le tick de `sym` à t (O(1))."""
        i = self.sidx[sym]
        j = self._advance(sym, t)
        ts, bs, as_, _ = self.data[sym]
        if j >= 0 and ts[j] <= t:                        # jamais de quote future
            self.bid[i] = bs[j]; self.ask[i] = as_[j]; self.last_t[i] = ts[j]
        else:
            self.bid[i] = np.nan; self.ask[i] = np.nan; self.last_t[i] = -np.inf

    def sync(self, t):
        """Avance tous les symboles au dernier tick <= t (pour les points hors événement)."""
        for s in self.data:
            i = self.sidx[s]
            j = self._advance(s, t)
            ts, bs, as_, _ = self.data[s]
            if j >= 0 and ts[j] <= t:                    # jamais de quote future
                self.bid[i] = bs[j]; self.ask[i] = as_[j]; self.last_t[i] = ts[j]
            else:
                self.bid[i] = np.nan; self.ask[i] = np.nan; self.last_t[i] = -np.inf

    def usd_price(self, ok):
        up = np.full(8, np.nan); up[0] = 1.0
        for i, c in enumerate(CURR):
            if c == "USD":
                continue
            sj = self.sidx.get(c + "USD")
            direct = sj is not None
            if not direct:
                sj = self.sidx.get("USD" + c)
            if sj is None or not ok[sj]:
                continue
            m = 0.5 * (self.bid[sj] + self.ask[sj])
            up[i] = m if direct else 1.0 / m
        return up

    def eval_at(self, t, fresh_ms, spread_cap, comm_scale=1.0, rows=None):
        """État courant -> (best_net, best_pi_global, best_ddd). Vectorisé.
        rows : sous-ensemble de chemins (IDX_FX / IDX_BT) ou None = tous."""
        ok = (self.last_t <= t) & ((t - self.last_t) <= fresh_ms)   # pas de quote future
        up = self.usd_price(ok)
        # validité des arêtes FX : fraîches + spread <= cap x médian (cap FX seul)
        spread_viol = (self.ask - self.bid) > spread_cap * self.MED
        fx_ok = ok & ~(spread_viol & self.IS_FX)
        R = slice(None) if rows is None else rows
        # taux dirigés par jambe de chemin : (P,3) — E = indice d'arête FX (0..27)
        E = self.P_E[R]
        ISM = self.P_ISM[R]
        has_leg = (E >= 0) | ISM                            # FX ou saut métal
        SI = np.where(E >= 0, self.E_S[np.maximum(E, 0)], -1)    # -> slist (-1 = non chargé)
        fx_ok_leg = np.where(E >= 0, (SI >= 0) & fx_ok[np.maximum(SI, 0)], True)
        # sauts métal : les deux dénominations chargées + fraîches ; le cap
        # spread s'applique aussi (médiane propre à l'instrument).
        metal_ok = ok & ~spread_viol
        M1 = self.P_M1[R]; M2 = self.P_M2[R]
        m1v = np.where(ISM, M1, 0); m2v = np.where(ISM, M2, 0)
        m_ok = np.where(ISM,
                        (M1 >= 0) & (M2 >= 0)
                        & metal_ok[np.maximum(m1v, 0)] & metal_ok[np.maximum(m2v, 0)],
                        True)
        leg_ok = np.where(has_leg, np.where(ISM, m_ok, fx_ok_leg), True)
        base_up = up[np.maximum(self.P_BASE[R], 0)]
        comm_leg = np.where(self.P_BASE[R] >= 0, COMM / np.where(base_up > 0, base_up, np.nan), 0.0)
        bid_e = np.where(SI >= 0, self.bid[np.maximum(SI, 0)], np.nan)
        ask_e = np.where(SI >= 0, self.ask[np.maximum(SI, 0)], np.nan)
        rate_fx = np.where(self.P_DIR[R] == 1, bid_e, 1.0 / np.where(ask_e > 0, ask_e, np.nan))
        # hop métal A->B : achat Métal-A à l'ask puis vente Métal-B au bid
        rate_m = self.bid[m2v] / np.where(self.ask[m1v] > 0, self.ask[m1v], np.nan)
        rate = np.where(ISM, rate_m, rate_fx)
        rate = np.where(has_leg & leg_ok, rate, 1.0)
        all_ok = leg_ok.all(1) & (self.P_L[R] > 0)
        prod = np.prod(rate, 1)
        up_s = up[self.P_S[R]]
        qty0 = NOTIONAL / np.where(up_s > 0, up_s, np.nan)
        qty_end = qty0 * prod
        up_end = up[self.P_END[R]]
        # unifié : FX -> qty_end x up[end] ; bout -> x ratio bid/ask (conversions s'annulent)
        has_b = self.P_HASB[R]
        PB = self.P_BOUT[R]
        bi_arr = np.maximum(PB, 0)
        b_ok = has_b & (PB >= 0) & ok[bi_arr]
        ratio_b = np.where(has_b,
                           self.bid[bi_arr] / np.where(self.ask[bi_arr] > 0, self.ask[bi_arr], np.nan),
                           1.0)
        usd_out = qty_end * up_end * ratio_b
        valid_path = all_ok & np.isfinite(usd_out) & np.isfinite(up_s) & (~has_b | b_ok)
        comm_fx = comm_leg.sum(1) * comm_scale
        net = np.where(valid_path & np.isfinite(comm_fx),
                       usd_out - NOTIONAL - comm_fx - self.P_CB[R], -np.inf)
        pi = int(np.argmax(net))
        if not np.isfinite(net[pi]):
            return None
        gpi = pi if rows is None else int(rows[pi])
        return float(net[pi]), gpi, float(usd_out[pi] - NOTIONAL)

    # ---------- exécution E1-E9 ----------
    def quote_at(self, sym, t):
        ts, bs, as_, _ = self.data[sym]
        j = np.searchsorted(ts, t, "right") - 1
        return (ts[j], bs[j], as_[j], j) if j >= 0 else (-1, np.nan, np.nan, -1)

    def exec_seq(self, t0, pi, lat_ms, tau_slip_bp, q_min):
        path = self.paths[pi]
        legs = [e for e in path if not (isinstance(e, tuple) and e[0] == "B")]
        bout = self.P_HASB[pi]
        if any(isinstance(ei, int) and FX[ei] not in self.data for ei in legs):
            return None
        if bout and (self.P_BOUT[pi] < 0 or self.slist[self.P_BOUT[pi]] not in self.data):
            return None
        self.sync(t0)
        ok = np.ones(len(self.slist), bool)
        up = self.usd_price(ok)
        start = self.p_start[pi]
        qty = NOTIONAL / up[start]
        cash = 0.0; comm = 0.0; t = t0; filled = 0; node = start; parts = 0
        for k, ei in enumerate(legs + ([-1] if bout else [])):
            t_f = t + lat_ms
            if ei == -1:
                bi = self.P_BOUT[pi]; sym = self.slist[bi]
                jt, bid_f, ask_f, j = self.quote_at(sym, t_f)
                if j < 0 or t_f - jt > 500:
                    return self._abort("bout stale", filled, cash, comm, t - t0, parts)
                comm += self.P_CB[pi]
                usd_end = qty * (bid_f / ask_f) * up[node]   # node = devise IC du bout
                t = t_f
                return dict(res="done", pnl=usd_end + cash - NOTIONAL - comm, why="bout",
                            fills=filled + 1, dur=t - t0, parts=parts)
            if isinstance(ei, tuple):
                # Saut métal A->B : ordre 1 achète Métal-A à l'ask (paye A,
                # reçoit métal), ordre 2 vend Métal-B au bid (reçoit B).
                # Non atomique : exposition métal entre les deux fills —
                # résiduel liquidé Convention A dans la devise de départ.
                m, to = ei[1], ei[2]
                s1 = METAL_SYM[m][CURR[node]]; s2 = METAL_SYM[m][CURR[to]]
                if s1 not in self.data or s2 not in self.data:
                    return None
                slip = tau_slip_bp * 1e-4
                # ordre 1 : A -> métal (achat à l'ask, sémantique d=-1)
                jt0, _, as0, _ = self.quote_at(s1, t)
                q_send = 1.0 / as0
                jt, bid1, ask1, j1 = self.quote_at(s1, t_f)
                if j1 < 0 or not np.isfinite(q_send) or q_send <= 0:
                    return self._abort("no quote", filled, cash, comm, t - t0, parts)
                q_now = 1.0 / ask1
                move = abs(q_now / q_send - 1.0)
                if move > 3 * slip:
                    return self._abort("kill IOC", filled, cash, comm, t - t0, parts)
                frac = 1.0 if move <= slip else 0.6
                if frac < q_min:
                    return self._abort("q<q_min", filled, cash, comm, t - t0, parts)
                if frac < 1.0:
                    cash += qty * (1 - frac) * up[node]
                    parts += 1
                qty_m = qty * frac * q_now      # métal reçu (unités métal)
                comm += METAL_COMM.get(s1, 0.0)
                filled += 1
                # ordre 2 : métal -> B (vente au bid, sémantique d=+1)
                t_f2 = t_f + lat_ms
                jt0b, b0s2, _, _ = self.quote_at(s2, t_f)
                q_send2 = b0s2
                jt, bid2, _, j2 = self.quote_at(s2, t_f2)
                # liquidation Convention A du résiduel métal dans `node`
                def liq_metal():
                    _, bid1_now, _, jl = self.quote_at(s1, t_f2)
                    return qty_m * (bid1_now if jl >= 0 else bid1) * up[node]
                if j2 < 0 or not np.isfinite(q_send2) or q_send2 <= 0:
                    cash += liq_metal()
                    return self._abort("no quote m2", filled, cash, comm, t_f2 - t0, parts)
                q_now2 = bid2
                move2 = abs(q_now2 / q_send2 - 1.0)
                if move2 > 3 * slip:
                    cash += liq_metal()
                    return self._abort("kill IOC m2", filled, cash, comm, t_f2 - t0, parts)
                frac2 = 1.0 if move2 <= slip else 0.6
                if frac2 < q_min:
                    cash += liq_metal()
                    return self._abort("q<q_min m2", filled, cash, comm, t_f2 - t0, parts)
                if frac2 < 1.0:
                    # métal invendu -> liquidation Convention A à la quote
                    # fraîche de la dénomination de départ
                    cash += (1 - frac2) * liq_metal()
                    parts += 1
                qty = qty_m * frac2 * q_now2
                comm += METAL_COMM.get(s2, 0.0)
                filled += 1
                node = to
                t = t_f2
                continue
            s = FX[ei]; base_i, quote_i = self.fe[s]
            d = +1 if node == base_i else -1
            jt0, bs0, as0, _ = self.quote_at(s, t)
            q_send = bs0 if d == +1 else 1.0 / as0
            jt, bid_f, ask_f, j = self.quote_at(s, t_f)
            if j < 0 or not np.isfinite(q_send) or q_send <= 0:
                return self._abort("no quote", filled, cash, comm, t - t0, parts)
            q_now = bid_f if d == +1 else 1.0 / ask_f
            move = abs(q_now / q_send - 1.0)
            slip = tau_slip_bp * 1e-4
            if move > 3 * slip:
                return self._abort("kill IOC", filled, cash, comm, t - t0, parts)
            frac = 1.0 if move <= slip else 0.6
            if frac < q_min:
                return self._abort("q<q_min", filled, cash, comm, t - t0, parts)
            if frac < 1.0:
                cash += qty * (1 - frac) * up[node]
                qty *= frac
                parts += 1
            qty *= q_now
            comm += COMM / up[base_i] if np.isfinite(up[base_i]) else COMM
            filled += 1
            node = quote_i if node == base_i else base_i
            t = t_f
        usd_end = qty * up[node]
        return dict(res="done", pnl=usd_end + cash - NOTIONAL - comm, why="",
                    fills=filled, dur=t - t0, parts=parts)

    def _abort(self, why, filled, cash, comm, dur, parts=0):
        c = 12.0 * filled   # ~aller-retour demi-spread + comm par jambe remplie
        return dict(res="abort", why=why, pnl=-c + cash - comm, fills=filled,
                    dur=dur, parts=parts)

    # ---------- scan (1 passe) ----------
    def scan(self, fresh_ms=500, spread_cap=2.5, thresh=-30.0, out=None,
             move_gate=0.25, eval_every_ms=500):
        """-> candidats [(t, pi, net, ddd)] + épisodes [(t0, t1, pi, net_max)].
        N'évalue que quand la paire tickée a bougé > move_gate x spread médian
        depuis la dernière éval, ou toutes les eval_every_ms au minimum."""
        cands = []
        episodes = []
        open_ep = None
        last_eval_t = -1e18
        eval_mid = np.full(len(self.slist), np.nan)   # mids au moment de la dernière éval
        n_ev = n_eval = 0
        med = np.array([self.med_spread[s] for s in self.slist])
        for k in range(len(self.ev_t)):
            t = int(self.ev_t[k])
            i = self.ev_i[k]
            s = self.slist[i]
            self.tick(s, t)
            n_ev += 1
            mid = 0.5 * (self.bid[i] + self.ask[i])
            moved = not np.isfinite(eval_mid[i]) or abs(mid - eval_mid[i]) > move_gate * med[i]
            if not moved and t - last_eval_t < eval_every_ms:
                continue
            last_eval_t = t
            eval_mid = 0.5 * (self.bid + self.ask)
            n_eval += 1
            ev = self.eval_at(t, fresh_ms, spread_cap, rows=self.IDX_FX)
            if ev is None:
                continue
            net, pi, ddd = ev
            if net > thresh:
                ev2 = self.eval_at(t, fresh_ms, spread_cap)   # bouts inclus
                if ev2 and ev2[0] > net:
                    net, pi, ddd = ev2
                cands.append((t, pi, net, ddd))
            if net > 0:
                if open_ep is None:
                    open_ep = [t, pi, net]
                else:
                    open_ep[2] = max(open_ep[2], net)
            elif open_ep is not None:
                episodes.append((open_ep[0], t, open_ep[1], open_ep[2]))
                open_ep = None
        if open_ep is not None:
            episodes.append((open_ep[0], int(self.ev_t[-1]), open_ep[1], open_ep[2]))
        print(f"scan: {n_ev:,} events -> {n_eval:,} évals ({100*n_eval/max(n_ev,1):.1f}%)")
        if out:
            np.savez(out,
                     cands=np.array(cands),
                     episodes=np.array(episodes))
        return cands, episodes


if __name__ == "__main__":
    e = Engine()
