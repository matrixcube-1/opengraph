#!/usr/bin/env python3
"""HOLDOUT — téléchargement .bi5 jour non vu (2026-10-27, 28 FX).

Règles AGENTS.md : une seule file globale ~1 req/s ; pause longue au premier 403/429/WAF ;
reprise idempotente via ledger JSON. Sortie : /opt/arb/data/{SYM}/{YYYY-MM-DD}/{HH}.csv
"""
import json
import lzma
import os
import struct
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
DATA = Path("/opt/arb/data")
LEDGER = DATA / "poc_ledger.json"
PAUSE_S = int(os.environ.get("POC_PAUSE_S", "7200"))   # AGENTS.md : 2 h au premier blocage
RATE_S = float(os.environ.get("POC_RATE_S", "1.05"))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

DAYS = [(2026, 10, 27)]   # HOLDOUT — jour non vu (mar. apres lun. 26)

# Annexe J : symbole Dukascopy -> niveau de référence (auto-détection du diviseur de prix)
UNIVERSE = {
    # 28 paires FX (K8)
    "EURUSD": 1.115, "GBPUSD": 1.332, "USDJPY": 157.7, "USDCHF": 0.828, "AUDUSD": 0.704,
    "USDCAD": 1.403, "NZDUSD": 0.570, "EURGBP": 0.837, "EURJPY": 175.8, "EURCHF": 0.924,
    "EURAUD": 1.584, "EURCAD": 1.565, "EURNZD": 1.956, "GBPJPY": 210.2, "GBPCHF": 1.103,
    "GBPAUD": 1.892, "GBPCAD": 1.869, "GBPNZD": 2.335, "AUDJPY": 111.0, "AUDCHF": 0.583,
    "AUDCAD": 0.988, "AUDNZD": 1.234, "CADJPY": 112.4, "CADCHF": 0.590, "CHFJPY": 190.5,
    "NZDJPY": 89.9, "NZDCAD": 0.800, "NZDCHF": 0.472,
    # 32 bouts (J.3) — niveaux indicatifs oct. 2026
    "DEUIDXEUR": 24500, "ITAIDXEUR": 42000, "FRAIDXEUR": 8100, "EUSIDXEUR": 5600,
    "NLDIDXEUR": 930, "XAUEUR": 3600, "XAGEUR": 43, "GBRIDXGBP": 9600, "XAUGBP": 3000,
    "JPNIDXJPY": 49000, "XAUJPY": 630000, "XAUCHF": 3300, "AUSIDXAUD": 8900,
    "XAUAUD": 5700, "XAGAUD": 68, "USA30IDXUSD": 47000, "USA500IDXUSD": 6800,
    "USSC2000IDXUSD": 2400, "USATECHIDXUSD": 25500, "CHEIDXCHF": 12500, "XAUUSD": 4000,
    "XAGUSD": 48, "LIGHTCMDUSD": 60, "BRENTCMDUSD": 65, "XPTCMDUSD": 1500,
    "XPDCMDUSD": 1350, "SUGARCMDUSD": 15, "COCOACMDUSD": 6000, "COFFEECMDUSX": 380,
    "COTTONCMDUSX": 64, "SOYBEANCMDUSX": 1000, "OJUICECMDUSX": 270,
}
DIVISORS = (1, 10, 100, 1000, 10000, 100000)
REC = struct.Struct(">iiiff")  # ms_off, ask, bid, ask_vol, bid_vol


def load_ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def save_ledger(led):
    LEDGER.write_text(json.dumps(led, indent=0, sort_keys=True))


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


BACKOFF_503 = (15, 45, 120)   # tarpit : les délais longs ne changent pas le débit amont


def fetch_hour(url, led, key):
    """-> (code, raw|None). 503 : retries en backoff ; 403/429 : pause longue."""
    for attempt, bo in enumerate([0, *BACKOFF_503]):
        if bo:
            print(f"  retry {key} dans {bo}s", flush=True)
            time.sleep(bo)
        try:
            return fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < len(BACKOFF_503):
                continue
            if e.code in (403, 429):
                led[key] = {"ok": False, "http": e.code}
                save_ledger(led)
                print(f"!!! {e.code} sur {key} — pause {PAUSE_S}s (règle WAF)", flush=True)
                time.sleep(PAUSE_S)
                return ("WAF", None)
            return (e.code, None)
        except Exception as e:
            if attempt < len(BACKOFF_503):
                print(f"  err {type(e).__name__} sur {key}, retry", flush=True)
                continue
            return ("ERR:" + type(e).__name__, None)
    return ("ERR", None)


def pick_divisor(ask_int, ref):
    import math
    best, bd = None, 1e9
    for d in DIVISORS:
        v = ask_int / d
        if v <= 0:
            continue
        score = abs(math.log(v / ref))
        if score < bd:
            bd, best = score, d
    return best, bd  # bd = |log ratio| ; < ~1.4 si correct


def decode(sym, raw, led):
    data = lzma.decompress(raw)
    n = len(data) // REC.size
    key = f"{sym}"
    if key not in led.get("_div", {}):
        ask0 = struct.unpack(">i", data[4:8])[0]
        d, score = pick_divisor(ask0, UNIVERSE[sym])
        led.setdefault("_div", {})[key] = {"div": d, "score": round(score, 3)}
        if score > 1.5:
            led.setdefault("_warn", []).append(f"{sym}: diviseur douteux {d} (score {score:.2f})")
    div = led["_div"][key]["div"]
    rows, bad_px, bad_seq, bad_ms, bad_vol = [], 0, 0, 0, 0
    last_ms = -1
    for i in range(n):
        ms, ask, bid, av, bv = REC.unpack_from(data, i * REC.size)
        a, b = ask / div, bid / div
        if b > a:
            bad_px += 1
        if ms <= last_ms:
            bad_seq += 1
        if ms < 0 or ms >= 3_600_000:
            bad_ms += 1
        if av <= 0 or bv <= 0:
            bad_vol += 1
        last_ms = ms
        rows.append((ms, a, b, av, bv))
    return rows, bad_px, bad_seq, bad_ms, bad_vol


FX_FIRST = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD","EURGBP",
            "EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY","GBPCHF","GBPAUD",
            "GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD","AUDNZD","CADJPY","CADCHF",
            "CHFJPY","NZDJPY","NZDCAD","NZDCHF"]
ORDER = FX_FIRST   # FX pur (Convention A) — pas les bouts CFD


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    led = load_ledger()
    t_last = 0.0
    for pass_ in range(6):                 # repasse sur les échecs (ledger ok=False)
        total_done = total_skip = total_fail = 0
        n_before = sum(1 for k, v in led.items() if not k.startswith("_") and v.get("ok"))
        for sym in ORDER:                  # 28 paires FX d'abord, puis les bouts
            for (y, mo, da) in DAYS:
                daydir = DATA / sym / f"{y}-{mo:02d}-{da:02d}"
                daydir.mkdir(parents=True, exist_ok=True)
                for h in range(24):
                    key = f"{sym}/{y}-{mo:02d}-{da:02d}/{h:02d}"
                    if led.get(key, {}).get("ok"):
                        total_skip += 1
                        continue
                    out = daydir / f"{h:02d}.csv"
                    url = BASE.format(sym=sym, y=y, m=mo - 1, d=da, h=h)  # mois 0-indexé
                    wait = RATE_S - (time.time() - t_last)
                    if wait > 0:
                        time.sleep(wait)
                    t0 = time.time()
                    code, raw = fetch_hour(url, led, key)
                    t_last = time.time()
                    if code == 404:
                        led[key] = {"ok": True, "ticks": 0, "empty": True}
                        save_ledger(led); continue
                    if code == "WAF" or code != 200 or raw is None:
                        led[key] = {"ok": False,
                                    "http": code if isinstance(code, int) else None,
                                    "err": str(code)[:100]}
                        save_ledger(led); total_fail += 1; continue
                    if len(raw) < 20:
                        led[key] = {"ok": True, "ticks": 0, "empty": True}
                        save_ledger(led); continue
                    try:
                        rows, bad_px, bad_seq, bad_ms, bad_vol = decode(sym, raw, led)
                    except Exception as e:
                        led[key] = {"ok": False, "err": f"decode {str(e)[:80]}"}
                        save_ledger(led); total_fail += 1
                        continue
                    with open(out, "w") as f:
                        f.write("ms_off,ask,bid,ask_vol,bid_vol\n")
                        for ms, a, b, av, bv in rows:
                            f.write(f"{ms},{a:.10g},{b:.10g},{av:.4g},{bv:.4g}\n")
                    led[key] = {"ok": True, "ticks": len(rows), "bad_px": bad_px, "bad_seq": bad_seq,
                                "bad_ms": bad_ms, "bad_vol": bad_vol,
                                "dt": round(time.time() - t0, 2)}
                    save_ledger(led)
                    total_done += 1
                    if total_done % 100 == 0:
                        print(f"... {total_done} fichiers ({sym} {h:02d}h), skip={total_skip} fail={total_fail}", flush=True)
        n_after = sum(1 for k, v in led.items() if not k.startswith("_") and v.get("ok"))
        print(f"== passe {pass_}: +{n_after - n_before} fichiers (total {n_after}) ==", flush=True)
        if n_after == n_before:
            break                            # plus rien ne passe -> stop
    print(f"FINI (passe {pass_}) done={total_done} skip={total_skip} fail={total_fail}", flush=True)


if __name__ == "__main__":
    main()
