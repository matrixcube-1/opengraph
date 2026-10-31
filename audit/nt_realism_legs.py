#!/usr/bin/env python3
"""Paliers de realisme NautilusTrader — runbook etape 5.

Un seul reglage a la fois, PnL mesure a chaque palier, comparaison
differentielle via diff_runs.py :

- `--mode internal` : exec_seq deterministe (references de palier) ;
- `--mode real`     : ordres LIMIT_IOC reels dans la venue simulee +
                      `exec_settle` Rust (meme arithmetique de bouclage).

Verrous identiques des deux cotes : `t_free` avant l'eval (le flag
`in_flight` couvre la duree reelle de la sequence), `ddd_min` avant
l'exec, `cooldown_s`.

Semantiques NT 1.230.0 mesurees par la sonde (test_nt_ioc.py) :

- IOC plafonne a la taille affichee L1 (fill partiel + cancel reliquat) ;
- fill au prix L1 exact, amelioration de prix si le livre s'est ameliore ;
- limite non franchissante -> CANCELED sans fill ;
- latence = relachement de l'ordre au premier tick >= t_envoi + L,
  matche contre le livre de ce tick (quantifie aux evenements donnees) ;
- `LatencyModel` prend des NANOSECONDES : `LatencyModel(lat_ms*1e6)`.

Modele "file d'attente / slippage" (palier D — documente) :

- L1 top-of-book uniquement : un IOC taker consomme au plus la taille
  affichee au meilleur niveau ; pas de marche du carnet (niveaux 2+
  inobservables) -> un ordre trop gros PARTIALISE au lieu de glisser ;
- pas de file pour un taker IOC ; le risque de file (consommation par
  d'autres preneurs entre decision et arrivee) est capture par les
  paliers de latence (le livre evolue pendant [t, t+lat]) ;
- l'heuristique exec_seq "partiel 60 % sur move modere" n'a pas
  d'equivalent venue : limite franchie -> fill, non franchie -> 0.
  C'est precisement ce que le palier A mesure.

Volume : les CSV sont en millions de base -> sizes NT = vol*1e6 unites
(la venue plafonne alors les fills comme exec_seq.vol_guard le ferait).

Usage:
  venv-og/bin/python nt_realism.py --mode internal [--lat-ms 0]
      [--vol-guard] [--step 20] [--out ref.json]
  venv-og/bin/python nt_realism.py --mode real [--lat-ms 0]
      [--step 20] [--out run.json]
"""
import argparse
import csv
import json
import math
import sys
import time
from calendar import timegm
from pathlib import Path
from time import strptime

import numpy as np

sys.path.insert(0, "/opt/arb/poc")
import poc_engine  # enum_paths seulement (echantillon 3 jambes)
import opengraph
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.common.component import (
    is_logging_initialized, set_logging_pyo3,
)
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    AccountType, BookType, OmsType, OrderSide, OrderStatus, TimeInForce,
)
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.test_kit.providers import TestInstrumentProvider

DATA = Path("/opt/arb/data")
POC = Path("/opt/arb/poc")
SIM = Venue("SIM")

# indices devises Rust (engine/src/universe.rs) — resid marque par ccy
CURR = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
      "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
      "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
      "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
      "CADJPY", "CADCHF", "CHFJPY", "NZDJPY", "NZDCAD", "NZDCHF"]
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]
P = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
         tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["internal", "real"], default="internal")
ap.add_argument("--lat-ms", type=float, default=2.5,
                help="internal: lat exec_seq par jambe ; real: latence venue")
ap.add_argument("--vol-guard", action="store_true",
                help="internal seulement : garde volume top-of-book")
ap.add_argument("--queue", action="store_true",
                help="real : modele file/slippage L1 (spr*(hq_sp + kq*frac))")
ap.add_argument("--hq-sp", type=float, default=0.1,
                help="haircut file invisible (fracao do spread)")
ap.add_argument("--kq", type=float, default=0.5,
                help="impact : frac consommee do L1 * kq spreads")
ap.add_argument("--step", type=int, default=20)
ap.add_argument("--days", type=str, default=None,
                help="sous-ensemble de DAYS, csv — filtre ticks ET candidats")
ap.add_argument("--cand", type=str, default="cand.npz",
                help="fichier candidats dans POC (holdout: cand_holdout.npz)")
ap.add_argument("--perf", action="store_true",
                help="mesures ns (calc/e2e) + RSS")
ap.add_argument("--gcfreeze", action="store_true",
                help="gc.freeze() apres chargement des ticks")
ap.add_argument("--out", type=str, default=None)
ap.add_argument("--ddd-min", type=float, default=None,
                help="override P['ddd_min'] (seuil declenchement)")
args = ap.parse_args()
if args.ddd_min is not None:
    P["ddd_min"] = args.ddd_min
OUT = Path(args.out) if args.out else POC / f"nt_{args.mode}_{args.lat_ms:g}.json"


class CandRealism(Strategy):
    """Rejoue les candidats ; exec_seq interne ou ordres IOC reels."""

    def on_start(self):
        for iid in self.iids:
            self.subscribe_quote_ticks(iid)

    def on_quote_tick(self, tick: QuoteTick):
        if self.perf:
            self._tick_ns = time.perf_counter_ns()
            if self.n_ticks % 200_000 == 0:
                with open("/proc/self/status") as f:
                    for ln in f:
                        if ln.startswith("VmRSS"):
                            self.rss.append(
                                (tick.ts_event // 1_000_000,
                                 int(ln.split()[1]) // 1024))
                            break
        self.n_ticks += 1
        t_ms = tick.ts_event // 1_000_000
        while self.ic < len(self.cand_t) and self.cand_t[self.ic] <= t_ms:
            self._process(int(self.cand_t[self.ic]))
            self.ic += 1

    def _process(self, t):
        if t < self.t_free or self.in_flight:     # verrou cooldown
            return
        _c0 = time.perf_counter_ns() if self.perf else 0
        ev = self.og.eval_at(t, P["fresh_ms"], P["spread_cap"],
                             P["comm_scale"])
        if self.perf:
            self.calc_ns.append(time.perf_counter_ns() - _c0)
        if ev is None:
            self.out.append({"t": t, "none": True})
            return
        pi, net, ddd = ev
        self.out.append({"t": t, "net": net, "ddd": ddd,
                         "legs": self.og.legs(pi),
                         "start": self.og.start(pi)})
        if net < P["ddd_min"]:                    # porte ddd_min
            return
        if self.mode == "internal":
            self._exec_internal(t, pi)
        else:
            self._exec_real(t, pi)

    def _legs_of(self, pi):
        """Jambes resolues : (sym, d, node_from, node_to) — node_to =
        l'autre noeud de la paire. Valide la chaine sur path_info."""
        legs = self.legmap.get(pi)
        if legs is not None:
            return legs
        info = self.og.path_info(pi)
        legs = []
        for sym, d, node_from, _base in info:
            q = CURR.index(FX[sym][3:] if d == 1 else FX[sym][:3])
            legs.append((sym, d, node_from, q))
        assert legs[0][2] == CURR.index(self.og.start(pi))
        for i in range(len(legs) - 1):
            assert legs[i][3] == legs[i + 1][2], "chaine de noeuds rompue"
        assert legs[-1][3] == self.og.end_node(pi)
        self.legmap[pi] = legs
        return legs

    # ---- mode internal : reference exec_seq ----

    def _exec_internal(self, t, pi):
        _c0 = time.perf_counter_ns() if self.perf else 0
        r = self.og.exec_seq(t, pi, self.lat_ms, P["tau_slip_bp"],
                             P["q_min"], self.vol_guard)
        if self.perf:
            self.exec_ns.append(time.perf_counter_ns() - _c0)
        if r is None:
            self.out.append({"exec": {"t": t, "lat": self.lat_ms,
                                      "res": None, "pi": pi,
                                      "legs": self.og.legs(pi),
                                      "start": self.og.start(pi)}})
            return
        res, pnl, fills, parts, dur = r
        self.out.append({"exec": {"t": t, "lat": self.lat_ms, "res": res,
                                  "pnl": pnl, "fills": fills,
                                  "parts": parts, "dur": dur,
                                  "pi": pi, "legs": self.og.legs(pi),
                                  "start": self.og.start(pi)}})
        self.t_free = t + max(dur, 1) + P["cooldown_s"] * 1000

    # ---- mode real : ordres LIMIT_IOC + settle Rust ----

    def _exec_real(self, t, pi):
        q0 = self.og.start_qty(t, pi)
        if q0 is None:                            # up[start] indispo -> None
            self.out.append({"exec": {"t": t, "lat": self.lat_ms,
                                      "res": None, "pi": pi,
                                      "legs": self.og.legs(pi),
                                      "start": self.og.start(pi)}})
            return
        self.seq = dict(pi=pi, t0=t, legs=self._legs_of(pi), acc=q0,
                        fills=[], li=0, last_ts=t, caps=0)
        self.in_flight = True
        self._submit_leg()

    def _submit_leg(self):
        s = self.seq
        sym, d, _nf, _nt = s["legs"][s["li"]]
        iid = self.iids[sym]
        q = self.cache.quote_tick(iid)
        if q is None:
            self._abort("no quote")
            return
        ask, bid = float(q.ask_price), float(q.bid_price)
        # limite IOC = touche +/- la bande kill d'exec_seq (3*tau_slip) :
        # une limite a la touche exacte annule sur le moindre mouvement
        # adverse alors qu'exec_seq tolere jusqu'a 3*tau avant le kill.
        tol = 3.0 * P["tau_slip_bp"] * 1e-4
        if d == 1:
            side, px, qty_base = OrderSide.SELL, bid * (1.0 - tol), s["acc"]
        else:
            side, px, qty_base = OrderSide.BUY, ask * (1.0 + tol), s["acc"] / ask
        instr = self.instrs[sym]
        qty = instr.make_qty(qty_base)
        if float(qty) <= 0 or not math.isfinite(px) or px <= 0:
            self._abort("no quote")
            return
        o = self.order_factory.limit(
            instrument_id=iid, order_side=side, quantity=qty,
            price=instr.make_price(px), time_in_force=TimeInForce.IOC)
        # palier D : contexte de file capture au send — taille affichee L1
        # (JA em unites — load_ticks alimenta vol*1e6) et spread relatif ;
        # le fill effectif est degrade en _resolve_leg selon la fraction.
        disp = float(q.bid_size) if d == 1 else float(q.ask_size)
        mid = (ask + bid) / 2.0
        spr = (ask - bid) / mid if mid > 0 else 0.0
        self.pending[str(o.client_order_id)] = (
            s["li"], float(qty), d, s["acc"], disp, spr)
        self.submit_order(o)
        if self.perf and s["li"] == 0 and self._tick_ns:
            # e2e : arrivee du tick declencheur -> retour de submit_order
            self.e2e_ns.append(time.perf_counter_ns() - self._tick_ns)

    def on_order_filled(self, event):
        oid = str(event.client_order_id)
        if oid not in self.pending or self.seq is None:
            return
        cq, cpx = self.pxacc.get(oid, (0.0, 0.0))
        self.pxacc[oid] = (cq + float(event.last_qty),
                           cpx + float(event.last_qty) * float(event.last_px))
        o = self.cache.order(event.client_order_id)
        if o is not None and o.status == OrderStatus.FILLED:
            self._resolve_leg(oid, event.ts_event)

    def on_order_canceled(self, event):
        oid = str(event.client_order_id)
        if oid in self.pending:
            self._resolve_leg(oid, event.ts_event)

    def on_order_rejected(self, event):
        oid = str(event.client_order_id)
        if oid in self.pending:
            self._resolve_leg(oid, event.ts_event)

    def _resolve_leg(self, oid, ts_event):
        s = self.seq
        li, oq, d, acc, disp, spr = self.pending.pop(oid)
        s["last_ts"] = ts_event // 1_000_000
        cq, cpx = self.pxacc.pop(oid, (0.0, 0.0))
        if cq <= 0:
            self._abort("kill IOC")               # livre adverse -> pas de fill
            return
        if cq < oq:
            s["caps"] += 1                        # fill < qty = cap L1 real
        px_fill = cpx / cq                        # VWAP du fill (L1 : 1 px)
        if self.queue_model and disp > 0:
            # file/slippage sur L1-only : on ne voit ni la file ni le L2+,
            # on paie un cout monotone dans la fraction consommee, en
            # unites de SPREAD : h = spr*(hq_sp + kq*min(1, q/disp))
            h = spr * (self.hq_sp + self.kq * min(1.0, oq / disp))
            px_fill *= (1.0 - d * h)              # sell: prix baisse, buy: hausse
        frac_eff = cq / oq if d == 1 else cq * px_fill / acc
        if frac_eff < P["q_min"]:
            self._abort("q<q_min", partial=(d, cq, px_fill))
            return
        px_node = px_fill if d == 1 else 1.0 / px_fill
        if frac_eff < 1.0:
            s["fills"].append((frac_eff, px_node))
        else:
            s["fills"].append((1.0, px_node))
        s["acc"] = (acc * frac_eff) * px_node
        s["li"] += 1
        if s["li"] < len(s["legs"]):
            self._submit_leg()
        else:
            self._finish()

    def _resid_pieces(self, s, partial=None):
        """Position reelle laissee ouverte par la sequence (a marquer en
        fin de run). Neant si aucun fill n'a eu lieu (ordre jamais partiellement
        execute = pas de position reelle)."""
        li = s["li"]
        _sym, d, nf, nt = s["legs"][li]
        if partial is None:
            # jambe courante non executee ; residuel = acc des jambes
            # completees (vide si abort avant tout fill — ex. leg 0)
            return [[s["acc"], nf]] if s["fills"] else []
        d, cq, px = partial
        # la jambe avortee A partiellement execute : reliquat non depense
        # a node_from + produit partiel a node_to
        if d == 1:
            leftover, proceeds = s["acc"] - cq, cq * px
        else:
            leftover, proceeds = s["acc"] - cq * px, cq
        out = []
        if leftover > 0:
            out.append([leftover, nf])
        if proceeds > 0:
            out.append([proceeds, nt])
        return out

    def _finish(self):
        s = self.seq
        r = self.og.exec_settle(s["t0"], s["pi"], s["fills"], False)
        dur = s["last_ts"] - s["t0"]
        if r is None:
            self.out.append({"exec": {"t": s["t0"], "lat": self.lat_ms,
                                      "res": None, "pi": s["pi"],
                                      "legs": self.og.legs(s["pi"]),
                                      "start": self.og.start(s["pi"])}})
        else:
            pnl, filled, parts, resid_t0 = r
            self.out.append({"exec": {"t": s["t0"], "lat": self.lat_ms,
                                      "res": "done", "pnl": pnl,
                                      "fills": filled, "parts": parts,
                                      "dur": dur,
                                      "resid_t0": resid_t0,
                                      "caps": s.get("caps", 0),
                                      "resid": [[s["acc"],
                                                 self.og.end_node(s["pi"])]],
                                      "pi": s["pi"],
                                      "legs": self.og.legs(s["pi"]),
                                      "start": self.og.start(s["pi"])}})
        self.t_free = s["t0"] + max(dur, 1) + P["cooldown_s"] * 1000
        self.in_flight = False
        self.seq = None

    def _abort(self, why, partial=None):
        s = self.seq
        r = self.og.exec_settle(s["t0"], s["pi"], s["fills"], True)
        dur = s["last_ts"] - s["t0"]
        if r is None:
            self.out.append({"exec": {"t": s["t0"], "lat": self.lat_ms,
                                      "res": None, "pi": s["pi"],
                                      "legs": self.og.legs(s["pi"]),
                                      "start": self.og.start(s["pi"])}})
        else:
            pnl, filled, parts, resid_t0 = r
            self.out.append({"exec": {"t": s["t0"], "lat": self.lat_ms,
                                      "res": f"abort:{why}", "pnl": pnl,
                                      "pi": s["pi"],
                                      "legs": self.og.legs(s["pi"]),
                                      "start": self.og.start(s["pi"]),
                                      "fills": filled, "parts": parts,
                                      "dur": dur,
                                      "resid_t0": resid_t0,
                                      "caps": s.get("caps", 0),
                                      "resid": self._resid_pieces(s,
                                                                 partial)}})
        self.t_free = s["t0"] + max(dur, 1) + P["cooldown_s"] * 1000
        self.in_flight = False
        self.seq = None

    def on_stop(self):
        self._tick_ns = 0                        # execs residuels : pas d'e2e
        while self.ic < len(self.cand_t):
            self._process(int(self.cand_t[self.ic]))
            self.ic += 1
        if self.mode != "real":
            return
        # --- verif residuel : positions reelles ouvertes + balance ---
        acc = self.portfolio.account(SIM)
        m = acc.balance_total(Currency.from_str("USD")) if acc else None
        bal = float(m.as_f64() if m is not None
                    and hasattr(m, "as_f64") else (m or 0.0))
        poss = self.cache.positions_open()
        self.venue = {
            "balance_usd": bal,
            "n_positions_open": len(poss),
            "positions": sorted(
                ((str(p.instrument_id), float(p.signed_qty))
                 for p in poss), key=lambda x: -abs(x[1]))[:20],
        }


def load_ticks(instr, sym):
    """QuoteTicks NT depuis les CSV horaires — sizes REELLES (vol*1e6
    unites) pour que la venue plafonne les fills au top-of-book."""
    out = []
    for d in DAYS:
        dd = DATA / sym / d
        if not dd.exists():
            continue
        e0 = timegm(strptime(d, "%Y-%m-%d")) * 1000
        for f in sorted(dd.glob("*.csv")):
            base = e0 + int(f.stem) * 3_600_000
            with open(f) as fh:
                r = csv.reader(fh)
                next(r)
                for ms, a, b, av, bv in r:
                    t = (base + int(ms)) * 1_000_000
                    out.append(QuoteTick(
                        instrument_id=instr.id,
                        bid_price=instr.make_price(float(b)),
                        ask_price=instr.make_price(float(a)),
                        bid_size=instr.make_qty(max(1.0,
                                                    round(float(bv) * 1e6))),
                        ask_size=instr.make_qty(max(1.0,
                                                    round(float(av) * 1e6))),
                        ts_event=t, ts_init=t))
    return out


def main():
    t0 = time.time()
    if args.days:                              # sous-fenetre : ticks + cands
        DAYS[:] = args.days.split(",")
        lo = timegm(strptime(DAYS[0], "%Y-%m-%d")) * 1000
        hi = timegm(strptime(DAYS[-1], "%Y-%m-%d")) * 1000 + 86_400_000
    else:
        lo, hi = None, None
    z = np.load(POC / args.cand)
    cands = z["cands"]
    assert np.all(np.diff(cands[:, 0]) >= 0), f"{args.cand} non chronologique"
    if lo is not None:
        cands = cands[(cands[:, 0] >= lo) & (cands[:, 0] < hi)]

    paths, _ = poc_engine.enum_paths()
    idx = set(range(0, len(cands), args.step))
    for k in range(len(cands)):
        legs = [e for e in paths[int(cands[k, 1])] if not isinstance(e, tuple)]
        if len(legs) == 3:
            idx.add(k)
    cand_t = np.sort(cands[sorted(idx), 0])
    print(f"{len(cand_t):,} candidats (pas={args.step} + 3 jambes) — "
          f"mode={args.mode} lat={args.lat_ms} vg={args.vol_guard}",
          flush=True)

    og = opengraph.Engine(str(DATA), DAYS)
    assert og.n_paths() == 2072

    # batterie : logger WARNING pose avant l'engine — le kernel saute
    # son propre init (is_logging_initialized) et les EVT/ordre sont
    # sous le seuil (sinon ~5 lignes INFO par ordre = le run est 3x plus
    # lent). Voie pyo3 : common.component.init_logging panique en 1.230.
    if not is_logging_initialized():
        set_logging_pyo3(True)
        nautilus_pyo3.init_logging(
            trader_id=nautilus_pyo3.TraderId("TRADER-001"),
            instance_id=nautilus_pyo3.UUID4(),
            level_stdout=nautilus_pyo3.LogLevel.WARNING)
    engine = BacktestEngine()
    engine.add_venue(
        venue=SIM, oms_type=OmsType.HEDGING, account_type=AccountType.MARGIN,
        base_currency=Currency.from_str("USD"),
        starting_balances=[Money(10**12, Currency.from_str("USD"))],
        book_type=BookType.L1_MBP,
        latency_model=None if args.mode == "internal" or args.lat_ms == 0
        else LatencyModel(int(args.lat_ms * 1_000_000)),
    )
    strat = CandRealism()
    strat.iids, strat.instrs = [], []
    strat.cand_t, strat.og, strat.out = cand_t, og, []
    strat.ic, strat.t_free, strat.n_ticks = 0, -1e18, 0
    strat.mode, strat.lat_ms, strat.vol_guard = (
        args.mode, args.lat_ms, args.vol_guard)
    strat.queue_model, strat.hq_sp, strat.kq = (
        args.queue, args.hq_sp, args.kq)
    strat.in_flight, strat.seq = False, None
    strat.pending, strat.pxacc, strat.legmap = {}, {}, {}
    strat.venue = {}
    strat.perf = args.perf
    strat.calc_ns, strat.exec_ns, strat.e2e_ns = [], [], []
    strat.rss, strat._tick_ns = [], 0

    for s in FX:                          # ordre table FX — contrat d'ordre
        instr = TestInstrumentProvider.default_fx_ccy(
            s[:3] + "/" + s[3:], SIM)
        engine.add_instrument(instr)
        strat.iids.append(instr.id)
        strat.instrs.append(instr)
        ticks = load_ticks(instr, s)
        engine.add_data(ticks)
        print(f"  {s}: {len(ticks):,} ticks", flush=True)
        del ticks

    engine.add_strategy(strat)
    if args.gcfreeze:
        import gc
        gc.collect()
        gc.freeze()                  # heap de ticks -> generation permanente
        print("gc.freeze() applique", flush=True)
    print(f"chargement+run... ({time.time()-t0:.0f}s ecoulees)", flush=True)
    engine.run()

    # --- residuel marque a l'horizon de fin de run (mids finaux) ---
    last_t = int(cand_t[-1]) if len(cand_t) else 0
    marks = og.usd_marks(last_t)
    resid_usd = 0.0
    resid_t0_sum = 0.0
    n_resid = 0
    for e in strat.out:
        x = e.get("exec")
        if not x or "resid" not in x:
            continue
        n_resid += 1
        resid_t0_sum += x.get("resid_t0", 0.0)
        for qty, ccy in x["resid"]:
            resid_usd += qty * marks[ccy]
    summary = {
        "mode": args.mode, "lat_ms": args.lat_ms,
        "vol_guard": args.vol_guard, "step": args.step,
        "queue": args.queue, "hq_sp": args.hq_sp, "kq": args.kq,
        "days": DAYS, "gcfreeze": args.gcfreeze,
        "resid_usd_end": resid_usd, "resid_usd_t0": resid_t0_sum,
        "resid_drift": resid_usd - resid_t0_sum, "n_resid": n_resid,
        "venue": strat.venue,
    }
    if args.perf:
        def pc(v):                             # percentiles ns -> us
            if not v:
                return {}
            a = np.sort(np.asarray(v, dtype=np.float64)) / 1e3
            n = len(a)
            return {"n": n, "p50_us": float(a[int(0.50 * (n - 1))]),
                    "p95_us": float(a[int(0.95 * (n - 1))]),
                    "p99_us": float(a[int(0.99 * (n - 1))]),
                    "max_us": float(a[-1])}
        summary["perf"] = {
            "calc": pc(strat.calc_ns),         # eval_at seul
            "exec": pc(strat.exec_ns),         # exec_seq (internal)
            "e2e": pc(strat.e2e_ns),           # tick -> submit leg 0 (real)
            "rss_mb": {"samples": len(strat.rss),
                       "first": strat.rss[0][1] if strat.rss else None,
                       "last": strat.rss[-1][1] if strat.rss else None,
                       "max": max((r[1] for r in strat.rss), default=None),
                       "series": strat.rss[::20]},
        }
    OUT.write_text(json.dumps({"evals": strat.out,
                               "summary": summary}, indent=0))
    el = time.time() - t0
    nex = sum(1 for e in strat.out if "exec" in e)
    nev = len(strat.out) - nex
    print(f"-> {OUT} : {nev} evals + {nex} execs | {strat.n_ticks:,} ticks "
          f"en {el:.0f}s = {strat.n_ticks/max(el,1):,.0f} ticks/s")
    if args.perf:
        pf = summary["perf"]
        for k in ("calc", "exec", "e2e"):
            if pf.get(k):
                print(f"   {k:4s} p99={pf[k]['p99_us']:.0f}us "
                      f"max={pf[k]['max_us']:.0f}us (n={pf[k]['n']})")
        r = pf["rss_mb"]
        print(f"   RSS {r['first']} -> {r['last']} MB (max {r['max']}) "
              f"sur {r['samples']} echantillons | gc.freeze={args.gcfreeze}")
    print(f"   residuel {n_resid} seq : {resid_t0_sum:+,.2f} USD @t0 -> "
          f"{resid_usd:+,.2f} USD @fin (drift {resid_usd-resid_t0_sum:+,.2f}) "
          f"| venue: {strat.venue.get('n_positions_open', '-')} pos, "
          f"balance {strat.venue.get('balance_usd', 0):,.0f} USD")


if __name__ == "__main__":
    main()
