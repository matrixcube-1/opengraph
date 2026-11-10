#!/usr/bin/env python3
"""live_view.py — flux unifie temps reel : ticks + evenements PnL.

Merge par timestamp les deux journaux (ticks/ + journal principal) :
  ticks    HH:MM:SS.mmm  SYMBOL  bid / ask   bp
  events   HH:MM:SS.mmm  OPP/SEND/done/SETT/PnL intercales a leur place

Usage : python -u live_view.py [--all] [--pair EURUSD]
"""
import json
import os
import sys
import time
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
FX = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP","EURJPY","EURCHF",
      "EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF",
      "AUDCAD","AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"]

def fmt_ts(ms):
    t = datetime.datetime.fromtimestamp(ms / 1000)
    return "%s.%03d" % (t.strftime("%H:%M:%S"), t.microsecond // 1000)

def fmt_event(d):
    ev, ts = d.get("ev"), d.get("ts", 0)
    if ev == "opportunity":
        return ">>> OPP  pi=%-5d net=+%.2f$ ddd=%.2f$ legs=%d" % (
            d["pi"], d["net_usd"], d["ddd_usd"], d["legs"])
    if ev == "leg_send":
        return "    SEND %-6s %-4s %8d @%.5f" % (
            FX[d["edge"]] if d["edge"] < len(FX) else "?",
            "BUY" if d["side"] == 1 else "SELL", d["volume"], d["limit_px"])
    if ev == "leg_fill":
        return "    done %-6s qty=%7d @%.5f%s" % (
            FX[d["edge"]] if d["edge"] < len(FX) else "?",
            d["qty_base"], d["px_fill"],
            " PARTIEL" if d.get("frac_eff", 1) < 0.999 else "")
    if ev == "settle":
        return "=== SETT pi=%-5d PnL=%+.2f$ dur=%dms" % (
            d["pi"], d["pnl_usd"], d["dur_ms"])
    if ev == "unwind_send":
        return "    UW-> %-6s %-4s %7d @%.5f" % (
            FX[d["edge"]] if d["edge"] < len(FX) else "?",
            "BUY" if d["side"] == 1 else "SELL", d["qty_base"], d["limit_px"])
    if ev == "unwind_fill":
        return "    UWok %-6s %7d @%.5f" % (
            FX[d["edge"]] if d["edge"] < len(FX) else "?",
            d["qty_base"], d["px_fill"])
    if ev == "system":
        return "    SYS  %s" % d["msg"]
    if ev == "residual":
        return "    RESID pi=%-5d resid=%.2f$" % (d["pi"], d["resid_usd"])
    return None

def main():
    replay_all = "--all" in sys.argv
    pair = None
    for i, a in enumerate(sys.argv):
        if a == "--pair" and i + 1 < len(sys.argv):
            pair = sys.argv[i + 1].upper()

    day = datetime.date.today().isoformat()
    ft = open(os.path.join(BASE, "journal", "ticks", day + ".jsonl"),
              "r", encoding="utf-8", errors="replace")
    fe = open(os.path.join(BASE, "journal", day + ".jsonl"),
              "r", encoding="utf-8", errors="replace")
    if not replay_all:
        ft.seek(0, os.SEEK_END)
        fe.seek(0, os.SEEK_END)

    print("=" * 66)
    print(" FLUX UNIFIE — ticks + PnL | %s | tri par timestamp" % (pair or "28 paires"))
    print("=" * 66)
    cum = 0.0
    buf = []
    last_flush = time.time()
    while True:
        for f, is_tick in ((ft, True), (fe, False)):
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if is_tick:
                    if d.get("ev") != "tick":
                        continue
                    sym = FX[d["e"]] if d["e"] < len(FX) else "e%d" % d["e"]
                    if pair and sym != pair:
                        continue
                    bid, ask = d["bid"], d["ask"]
                    bp = (ask - bid) / bid * 1e4 if bid else 0.0
                    buf.append((d["ts"], "tick",
                                "%-6s  %.5f / %.5f  %4.2f bp" % (sym, bid, ask, bp)))
                else:
                    out = fmt_event(d)
                    if out:
                        buf.append((d.get("ts", 0), "ev", out))
        # flush ordonne toutes les 300 ms pour laisser arriver l'interleave
        if buf and time.time() - last_flush > 0.3:
            buf.sort(key=lambda x: x[0])
            for ts, kind, s in buf:
                if kind == "ev" and "SETT" in s:
                    # cumule PnL dans la ligne settle
                    import re
                    m = re.search(r"PnL=([+-]?[\d.]+)\$", s)
                    if m:
                        cum += float(m.group(1))
                        s = s + " | CUM %+.2f$" % cum
                print("%s  %s%s" % (fmt_ts(ts),
                                    "  " if kind == "tick" else "",
                                    s))
            buf.clear()
            last_flush = time.time()

if __name__ == "__main__":
    main()
