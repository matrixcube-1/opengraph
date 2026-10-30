#!/usr/bin/env python3
"""Test : ordre des QuoteTicks a timestamp egal sous BacktestEngine.

Miroir du test Rust `ev_order_at_equal_timestamps_follows_fx_table` :
3 instruments (EURUSD=arete 0, CADJPY=arete 22, NZDCHF=arete 27), 2 ticks
chacun au MEME ts_event/ts_init + 1 tick ulterieur. La strategie enregistre
l'ordre d'arrivee dans on_quote_tick.

Attendu (ordre stable table FX) : EURUSD, EURUSD, CADJPY, CADJPY,
NZDCHF, NZDCHF — jamais l'ordre alphabetique (CADJPY < EURUSD < NZDCHF).
Controle negatif : donnees ajoutees dans un ordre different -> l'ordre
observe doit suivre l'ordre d'AJOUT des donnees, pas le nom du symbole.
"""
import sys
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.test_kit.providers import TestInstrumentProvider

SIM = Venue("SIM")
USN = 1_000_000_000  # 1 s en ns


class Recorder(Strategy):
    def __init__(self):
        super().__init__()
        self.seen = []

    def on_start(self):
        for iid in self.iids:
            self.subscribe_quote_ticks(iid)

    def on_quote_tick(self, tick: QuoteTick):
        self.seen.append(str(tick.instrument_id.symbol))


def build(instrument_syms):
    """BacktestEngine + instruments ajoutes dans l'ordre donne."""
    engine = BacktestEngine()
    engine.add_venue(
        venue=SIM,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=Currency.from_str("USD"),
        starting_balances=[Money(1_000_000, Currency.from_str("USD"))],
        book_type=BookType.L1_MBP,
        latency_model=None,  # mode deterministe zero-latence (parite)
    )
    instrs = {}
    for s in instrument_syms:
        instr = TestInstrumentProvider.default_fx_ccy(
            s[:3] + "/" + s[3:], SIM)
        engine.add_instrument(instr)
        instrs[s] = instr
    return engine, instrs


def ticks_of(instr, n_same, ts_same, ts_late):
    bid = instr.make_price(1.1)
    ask = instr.make_price(1.1001)
    size = instr.make_qty(1_000_000)
    out = [
        QuoteTick(
            instrument_id=instr.id,
            bid_price=bid,
            ask_price=ask,
            bid_size=size,
            ask_size=size,
            ts_event=ts_same,
            ts_init=ts_same,
        )
        for _ in range(n_same)
    ]
    out.append(QuoteTick(
        instrument_id=instr.id,
        bid_price=bid,
        ask_price=ask,
        bid_size=size,
        ask_size=size,
        ts_event=ts_late,
        ts_init=ts_late,
    ))
    return out


def run(order):
    """order = ordre d'ajout des instruments ET de leurs donnees."""
    engine, instrs = build(order)
    strat = Recorder()
    strat.iids = [instrs[s].id for s in order]
    engine.add_strategy(strat)
    for s in order:
        engine.add_data(ticks_of(instrs[s], 2, USN, USN + 500))
    engine.run()
    engine.dispose()
    return strat.seen


# NT >= 1.230 : le logger Rust ne se reinitialise pas dans le meme
# processus (panic "set a logger after initialized"). Un cas par
# processus : `python test_nt_order.py 1` puis `... 2`.
import sys
CASE = sys.argv[1] if len(sys.argv) > 1 else "all"

if CASE in ("1", "all"):
    # --- cas 1 : donnees groupees par instrument en ordre table FX ---
    seen = run(["EURUSD", "CADJPY", "NZDCHF"])
    print("ordre ajout FX table -> arrivee:", seen[:6], flush=True)
    at_same = [s.split(".")[0].replace("/", "") for s in seen[:6]]
    assert at_same == ["EURUSD", "EURUSD", "CADJPY", "CADJPY", "NZDCHF", "NZDCHF"], \
        f"ordre inattendu: {at_same}"
    print("CAS 1 OK : ordre table FX preserve a ts egal (pas alphabetique)", flush=True)

if CASE in ("2", "all"):
    # --- cas 2 : controle negatif — donnees ajoutees NZDCHF d'abord ---
    seen2 = run(["NZDCHF", "EURUSD", "CADJPY"])
    at_same2 = [s.split(".")[0].replace("/", "") for s in seen2[:6]]
    print("ordre ajout inverse -> arrivee:", at_same2, flush=True)
    if at_same2 == ["NZDCHF", "NZDCHF", "EURUSD", "EURUSD", "CADJPY", "CADJPY"]:
        print("CAS 2 : l'ordre suit l'ordre d'AJOUT DES DONNEES (stable ts_init)", flush=True)
    elif at_same2 == ["EURUSD", "EURUSD", "CADJPY", "CADJPY", "NZDCHF", "NZDCHF"]:
        print("CAS 2 : l'ordre suit l'enregistrement des INSTRUMENTS", flush=True)
    else:
        print(f"CAS 2 : ordre autre -> {at_same2} (a documenter)", flush=True)

print("TEST NT ORDRE TERMINE", flush=True)
