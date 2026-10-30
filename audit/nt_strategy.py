#!/usr/bin/env python3
"""Strategy NautilusTrader — passe de parite deterministe vs replay POC.

Mode PARITE (runbook etape 4 — le realisme/IOC vient apres) :

- `opengraph.Engine` PRECHARGE : `eval_at(t_cand)` rembobine les curseurs
  et donne l'etat exact a t — miroir du `eng.sync(t)` de `replay()`.
  Pas d'`ingest` ici : l'ordre d'arrivee des ticks a ts egal ne doit pas
  influencer l'etat evalue (le replay sync(t) voit TOUS les ticks <= t).
- NT delivre les vrais ticks (flux valide : ordre, livraison, throughput)
  — la Strategy s'en sert comme horloge pour avancer dans la liste des
  candidats.
- Memes verrous qu'aux memes endroits du replay : `t_free` (cooldown)
  avant l'eval, `ddd_min` avant l'exec. `exec_seq` deterministe interne
  — pas d'ordres Nautilus dans cette passe.
- Sortie au schema parity.rs : {"evals":[entree|{"exec":{...}}]},
  comparee a nt_ref.json via parity_cmp.py.

Usage: venv-og/bin/python nt_strategy.py [pas] [out.json]
"""
import csv
import json
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
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.test_kit.providers import TestInstrumentProvider

DATA = Path("/opt/arb/data")
POC = Path("/opt/arb/poc")
SIM = Venue("SIM")

# Annexe J — ordre table FX = ordre d'ajout des donnees (contrat d'ordre)
FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
      "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
      "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
      "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
      "CADJPY", "CADCHF", "CHFJPY", "NZDJPY", "NZDCAD", "NZDCHF"]
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]
P = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
         lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)
STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else POC / "nt_strat.json"


class CandParity(Strategy):
    """Rejoue les candidats aux memes timestamps que le flux de ticks."""

    def on_start(self):
        for iid in self.iids:
            self.subscribe_quote_ticks(iid)

    def on_quote_tick(self, tick: QuoteTick):
        self.n_ticks += 1
        t_ms = tick.ts_event // 1_000_000
        while self.ic < len(self.cand_t) and self.cand_t[self.ic] <= t_ms:
            self._process(int(self.cand_t[self.ic]))
            self.ic += 1

    def _process(self, t):
        if t < self.t_free:                       # verrou cooldown — replay()
            return
        ev = self.og.eval_at(t, P["fresh_ms"], P["spread_cap"],
                             P["comm_scale"])
        if ev is None:
            self.out.append({"t": t, "none": True})
            return
        pi, net, ddd = ev
        self.out.append({"t": t, "net": net, "ddd": ddd,
                         "legs": self.og.legs(pi),
                         "start": self.og.start(pi)})
        if net < P["ddd_min"]:                    # porte ddd_min — replay()
            return
        r = self.og.exec_seq(t, pi, P["lat_ms"], P["tau_slip_bp"],
                             P["q_min"], False)   # vol_guard=False — parite
        if r is None:
            self.out.append({"exec": {"t": t, "lat": P["lat_ms"],
                                      "res": None}})
            return
        res, pnl, fills, parts, dur = r
        self.out.append({"exec": {"t": t, "lat": P["lat_ms"], "res": res,
                                  "pnl": pnl, "fills": fills,
                                  "parts": parts, "dur": dur}})
        self.t_free = t + max(dur, 1) + P["cooldown_s"] * 1000

    def on_stop(self):
        while self.ic < len(self.cand_t):
            self._process(int(self.cand_t[self.ic]))
            self.ic += 1


def load_ticks(instr, sym):
    """QuoteTicks NT depuis les CSV horaires — meme source que les moteurs."""
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
                    # qty entiere (precision FX = 0) ; tailles inutilisees
                    # en parite (vol_guard=False, volumes lus cote Rust)
                    out.append(QuoteTick(
                        instrument_id=instr.id,
                        bid_price=instr.make_price(float(b)),
                        ask_price=instr.make_price(float(a)),
                        bid_size=instr.make_qty(max(float(bv), 1.0)),
                        ask_size=instr.make_qty(max(float(av), 1.0)),
                        ts_event=t, ts_init=t))
    return out


def main():
    t0 = time.time()
    z = np.load(POC / "cand.npz")
    cands = z["cands"]
    assert np.all(np.diff(cands[:, 0]) >= 0), "cand.npz non chronologique"

    paths, _ = poc_engine.enum_paths()
    idx = set(range(0, len(cands), STEP))
    for k in range(len(cands)):
        legs = [e for e in paths[int(cands[k, 1])] if not isinstance(e, tuple)]
        if len(legs) == 3:
            idx.add(k)
    cand_t = np.sort(cands[sorted(idx), 0])
    print(f"{len(cand_t):,} candidats echantillonnes (pas={STEP} + 3 jambes)",
          flush=True)

    og = opengraph.Engine(str(DATA), DAYS)
    assert og.n_paths() == 2072

    engine = BacktestEngine()
    engine.add_venue(
        venue=SIM, oms_type=OmsType.HEDGING, account_type=AccountType.MARGIN,
        base_currency=Currency.from_str("USD"),
        starting_balances=[Money(1_000_000, Currency.from_str("USD"))],
        book_type=BookType.L1_MBP,
        latency_model=None,                # deterministe zero-latence — parite
    )
    strat = CandParity()
    strat.iids, strat.cand_t, strat.og, strat.out = [], cand_t, og, []
    strat.ic, strat.t_free, strat.n_ticks = 0, -1e18, 0

    for s in FX:                          # ordre table FX — contrat d'ordre
        instr = TestInstrumentProvider.default_fx_ccy(
            s[:3] + "/" + s[3:], SIM)
        engine.add_instrument(instr)
        strat.iids.append(instr.id)
        ticks = load_ticks(instr, s)
        engine.add_data(ticks)
        print(f"  {s}: {len(ticks):,} ticks", flush=True)
        del ticks

    engine.add_strategy(strat)
    print(f"chargement+run... ({time.time()-t0:.0f}s ecoulees)", flush=True)
    engine.run()

    OUT.write_text(json.dumps({"evals": strat.out}, indent=0))
    el = time.time() - t0
    print(f"-> {OUT} : {len(strat.out)} entrees | {strat.n_ticks:,} ticks "
          f"en {el:.0f}s = {strat.n_ticks/max(el,1):,.0f} ticks/s")


if __name__ == "__main__":
    main()
