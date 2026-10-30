#!/usr/bin/env python3
"""Sonde empirique NautilusTrader — semantique des ordres LIMIT_IOC.

Questions auxquelles repond ce test (NT 1.230.0, serveur) :

1. Un IOC buy qty > ask_size : fill PLAFONNE a la taille affichee L1,
   ou fill integral (liquidite infinie) ?
2. Les evenements de fill arrivent-ils PENDANT on_quote_tick (synchrone)
   ou apres retour du handler (asynchrone, meme ts) ?
3. Quels callbacks : on_order_filled / on_order_canceled / rejected ?
4. Le prix de fill : ask exact (L1) ?
5. Avec LatencyModel, le fill arrive-t-il a t + lat_ns ?

Cas : 3 ticks EURUSD (ask 1.10 / size 200k), ordres IOC de tailles
variees envoyes depuis on_quote_tick / on_order_filled.
"""
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    AccountType, BookType, OmsType, OrderSide, TimeInForce,
)
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.test_kit.providers import TestInstrumentProvider

SIM = Venue("SIM")
T0 = 1_800_000_000_000_000_000   # ns arbitraire


class Probe(Strategy):
    def on_start(self):
        self.rec = []
        self.n_ticks = 0
        self.subscribe_quote_ticks(self.iid)

    def _log(self, msg):
        self.rec.append(f"[{self.clock.timestamp_ns() - T0:>12} ns] {msg}")

    def _ioc(self, side, qty, px, tag):
        o = self.order_factory.limit(
            instrument_id=self.iid, order_side=side,
            quantity=self.instr.make_qty(qty),
            price=self.instr.make_price(px),
            time_in_force=TimeInForce.IOC,
        )
        self.submit_order(o)
        self._log(f"submit {tag} oid={o.client_order_id} "
                  f"{side.name} qty={qty} px={px}")

    def on_quote_tick(self, tick: QuoteTick):
        self.n_ticks += 1
        t_rel = tick.ts_event - T0
        self._log(f"tick#{self.n_ticks} t={t_rel} "
                  f"bid={tick.bid_price} ask={tick.ask_price} "
                  f"asz={tick.ask_size}")
        if self.n_ticks == 1:
            # ordre 300k > ask_size 200k -> plafond ?
            self._ioc(OrderSide.BUY, 300_000, 1.10000, "IOC>size")
        elif self.n_ticks == 2:
            # ordre 50k < size -> fill complet attendu au ask
            self._ioc(OrderSide.BUY, 50_000, 1.10000, "IOC<size")
        elif self.n_ticks == 3:
            # limite SOUS le ask -> aucun fill attendu (non franchissant)
            self._ioc(OrderSide.BUY, 50_000, 1.09990, "IOC miss")
            # vente IOC au bid
            self._ioc(OrderSide.SELL, 50_000, 1.09980, "IOC sell")

    def on_order_filled(self, event):
        self._log(f"FILLED oid={event.client_order_id} "
                  f"px={event.last_px} qty={event.last_qty} "
                  f"cum={event.order_filled_qty if hasattr(event,'order_filled_qty') else '?'}")

    def on_order_canceled(self, event):
        self._log(f"CANCELED oid={event.client_order_id}")

    def on_order_rejected(self, event):
        self._log(f"REJECTED oid={event.client_order_id} {event.reason}")

    def on_order_expired(self, event):
        self._log(f"EXPIRED oid={event.client_order_id}")

    def on_order_accepted(self, event):
        self._log(f"ACCEPTED oid={event.client_order_id}")


def run(latency_ns):
    engine = BacktestEngine()
    engine.add_venue(
        venue=SIM, oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=Currency.from_str("USD"),
        starting_balances=[Money(10**12, Currency.from_str("USD"))],
        book_type=BookType.L1_MBP,
        latency_model=None if latency_ns == 0
        else __import__("nautilus_trader.backtest.models",
                        fromlist=["LatencyModel"]).LatencyModel(latency_ns),
    )
    instr = TestInstrumentProvider.default_fx_ccy("EUR/USD", SIM)
    engine.add_instrument(instr)
    s = Probe()
    s.iid, s.instr = instr.id, instr

    # asks variables : 1.10000 -> 1.09990 -> 1.10020 — sous latence, un
    # IOC limite 1.10 envoye a t=0 doit voir le livre d'arrivee (1.0999)
    ticks = []
    asks = [1.10000, 1.09990, 1.10020]
    for i in range(3):
        t = T0 + i * 1_000_000
        ticks.append(QuoteTick(
            instrument_id=instr.id,
            bid_price=instr.make_price(asks[i] - 0.00020),
            ask_price=instr.make_price(asks[i]),
            bid_size=instr.make_qty(200_000),
            ask_size=instr.make_qty(200_000),
            ts_event=t, ts_init=t))
    engine.add_data(ticks)
    engine.add_strategy(s)
    engine.run()
    print(f"=== latency_ns={latency_ns} ===")
    for line in s.rec:
        print(line)


if __name__ == "__main__":
    import sys
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
