#!/usr/bin/env python3
"""Audit multi-facteurs de la qualite tick Dukascopy vs besoins methodologie.

Facteurs verifies par fichier (symbole-mois):
 A. Structure    : colonnes attendues, parse, lignes > 0, derniere ligne non tronquee
 B. Timestamps   : ms epoch, monotone non-decroissant, dans la fenetre du mois,
                   granularite ms reellement utilisee (besoin: fenetre exec ~ms)
 C. Couverture   : heures UTC avec ticks vs heures de trading attendues (FX: dim 21h
                   -> ven 21h), ticks/jour, detection jours anormalement creux
 D. Quotes       : bid<=ask (violations), prix > 0, mediane dans bornes plausibles
 E. Spread       : median/p95/p99/max en pips -> compat cap 1.7x spread normal
 F. Microstruct. : gaps inter-tick (p99, max), plus longue run de quotes figees
                   (feed stale), volumes ask/bid renseignes (besoin: modele fills)
Sortie: /opt/arb/logs/audit_quality_report.{md,jsonl}
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

DATA = Path("/opt/arb/data/ticks")
OUT_MD = Path("/opt/arb/logs/audit_quality_report.md")
OUT_JSONL = Path("/opt/arb/logs/audit_quality_report.jsonl")

# Bornes de plausibilite prix + taille de pip par classe/symbole.
PIP = {s: 1e-4 for s in
       "EURUSD GBPUSD AUDUSD NZDUSD EURGBP EURAUD EURCAD EURNZD GBPAUD GBPCAD GBPNZD AUDCAD AUDNZD NZDCAD".split()}
PIP.update({s: 1e-2 for s in
            "USDJPY EURJPY GBPJPY AUDJPY CADJPY CHFJPY NZDJPY USDCHF EURCHF GBPCHF AUDCHF CADCHF NZDCHF XAUJPY".split()})
PIP.update({"XAUEUR": 0.1, "XAGEUR": 0.01, "XAUGBP": 0.1, "XAUAUD": 0.1, "XAGAUD": 0.01,
            "XAUUSD": 0.1, "XAGUSD": 0.01, "XAUCHF": 0.1})
BOUNDS = {"EURUSD": (0.9, 1.4), "GBPUSD": (1.0, 1.6), "USDJPY": (90.0, 200.0),
          "USDCHF": (0.7, 1.1), "AUDUSD": (0.5, 0.9), "USDCAD": (1.1, 1.6),
          "NZDUSD": (0.45, 0.8), "EURGBP": (0.75, 0.95), "EURJPY": (110.0, 200.0),
          "EURCHF": (0.85, 1.15), "EURAUD": (1.4, 2.0), "EURCAD": (1.3, 1.7),
          "EURNZD": (1.6, 2.1), "GBPJPY": (140.0, 260.0), "GBPCHF": (1.0, 1.5),
          "GBPAUD": (1.7, 2.3), "GBPCAD": (1.6, 2.1), "GBPNZD": (1.9, 2.6),
          "AUDJPY": (70.0, 130.0), "AUDCHF": (0.5, 0.8), "AUDCAD": (0.8, 1.05),
          "AUDNZD": (1.0, 1.2), "CADJPY": (80.0, 140.0), "CADCHF": (0.55, 0.8),
          "CHFJPY": (130.0, 220.0), "NZDJPY": (60.0, 110.0), "NZDCHF": (0.45, 0.7),
          "NZDCAD": (0.75, 1.0), "XAUUSD": (1800.0, 4500.0), "XAGUSD": (18.0, 60.0),
          "XAUEUR": (1700.0, 4200.0), "XAGEUR": (17.0, 55.0), "XAUGBP": (1500.0, 3600.0),
          "XAUJPY": (300000.0, 800000.0), "XAUCHF": (1700.0, 4000.0), "XAUAUD": (2900.0, 7000.0),
          "XAGAUD": (28.0, 90.0), "DE40": (15000.0, 26000.0), "IT40": (25000.0, 45000.0),
          "F40": (6500.0, 9000.0), "STOXX50": (4000.0, 5500.0), "NETH25": (700.0, 1100.0),
          "UK100": (7000.0, 9500.0), "JP225": (30000.0, 50000.0), "AUS200": (7000.0, 9500.0),
          "US30": (35000.0, 52000.0), "US500": (4500.0, 8000.0), "US2000": (1800.0, 2800.0),
          "USTEC": (16000.0, 28000.0), "SWI20": (10000.0, 14000.0), "XTIUSD": (55.0, 110.0),
          "XBRUSD": (60.0, 110.0), "XPTUSD": (800.0, 1200.0), "XPDUSD": (800.0, 1400.0),
          "SUGAR": (0.1, 0.35), "COCOA": (20.0, 140.0), "COFFEE": (1.0, 5.0),
          "COTTON": (0.5, 1.3), "SOYBEAN": (9.0, 20.0), "OJ": (1.0, 6.0)}

FX = set("EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD EURGBP EURJPY EURCHF EURAUD EURCAD EURNZD "
         "GBPJPY GBPCHF GBPAUD GBPCAD GBPNZD AUDJPY AUDCHF AUDCAD AUDNZD CADJPY CADCHF CHFJPY NZDJPY "
         "NZDCHF NZDCAD".split())


def trading_hours(y: int, m: int) -> set:
    """Heures UTC de trading attendues dans le mois (FX: dim 21h -> ven 21h).

    Pour les CFD la fenetre est plus etroite (heures de marche) -- on utilise le
    meme filtre comme borne superieure; le ratio de couverture se compare ensuite
    a la mediane du symbole, pas a 1.
    """
    if m == 12:
        nxt = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(y, m + 1, 1, tzinfo=timezone.utc)
    t = datetime(y, m, 1, tzinfo=timezone.utc)
    hrs = set()
    while t < nxt:
        wd, hh = t.weekday(), t.hour  # lun=0 ... dim=6
        if wd < 4 or (wd == 4 and hh < 21) or (wd == 6 and hh >= 21):
            hrs.add(int(t.timestamp() // 3600))
        t += timedelta(hours=1)
    return hrs


def audit_file(sym: str, f: Path):
    """Retourne (stats dict, warnings list) pour un csv mensuel."""
    w = []
    size = f.stat().st_size
    if size == 0:
        return {"symbol": sym, "file": f.name, "rows": 0, "bytes": 0}, ["FICHIER_VIDE"]
    try:
        df = pl.read_csv(f, columns=["timestamp", "askPrice", "bidPrice",
                                     "askVolume", "bidVolume"],
                         schema_overrides={"timestamp": pl.Int64})
    except Exception as e:
        return {"symbol": sym, "file": f.name, "rows": -1, "bytes": size}, [f"PARSE_FAIL:{e}"]
    n = df.height
    if n == 0:
        return {"symbol": sym, "file": f.name, "rows": 0, "bytes": size}, ["ZERO_LIGNES"]

    ts = df["timestamp"]
    ask = df["askPrice"]
    bid = df["bidPrice"]

    # B. timestamps
    epoch_ms = 1_000_000_000_000 < int(ts[0]) < 10_000_000_000_000
    if not epoch_ms:
        w.append("TS_PAS_MS")
    non_mono = int((ts.diff() < 0).sum())
    if non_mono:
        w.append(f"NON_MONOTONE:{non_mono}")
    dup = n - ts.n_unique()
    t0, t1 = int(ts[0]), int(ts[-1])
    ms_used = int(((ts % 1000) != 0).sum())
    if ms_used < n * 0.5:
        w.append("GRANULARITE_SECONDE")  # incompatible fenetre exec ms

    # mois attendu depuis le nom du fichier: sym-tick-YYYY-MM-DD-YYYY-MM-DD.csv
    parts = f.name.split("-")
    try:
        y, m = int(parts[2]), int(parts[3])
        d0 = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
        d1 = int(datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        hors = int(((ts < d0 - 7200e3) | (ts >= d1 + 7200e3)).sum())
        if hors > n * 0.01:
            w.append(f"TS_HORS_MOIS:{hors}")
        exp = trading_hours(y, m)
        got = set((ts // 3_600_000).unique().to_list())
        cov = len(got & exp) / max(1, len(exp))
        # chaque jour ouvre (lun-ven) du mois doit avoir des ticks
        days_got = set((ts // 86_400_000).unique().to_list())
        missing = []
        t = datetime(y, m, 1, tzinfo=timezone.utc)
        while t.month == m:
            if t.weekday() < 5 and int(t.timestamp() // 86400) not in days_got:
                missing.append(t.strftime("%m-%d"))
            t += timedelta(days=1)
        if missing:
            w.append(f"JOURS_OUVRES_MANQUANTS:{','.join(missing)}")
    except Exception:
        cov, hors, missing = -1.0, -1, []

    # C. couverture par jour
    day = (ts // 86_400_000)
    per_day = day.value_counts()
    min_day = int(per_day["count"].min()) if per_day.height else 0
    med_day = float(per_day["count"].median()) if per_day.height else 0.0
    if med_day and min_day < med_day * 0.15:
        w.append(f"JOUR_CREUX:{min_day}")

    # D. quotes
    v_bad = int((bid > ask).sum())
    if v_bad:
        w.append(f"BID_GT_ASK:{v_bad}")
    nonpos = int(((bid <= 0) | (ask <= 0)).sum())
    if nonpos:
        w.append(f"PRIX_NON_POSITIF:{nonpos}")
    med = float(ask.median())
    lo, hi = BOUNDS.get(sym, (0, float("inf")))
    if not (lo <= med <= hi):
        w.append(f"PRIX_HORS_BORNES:{med}")

    # E. spread
    pip = PIP.get(sym, 1e-4)
    sp = (ask - bid) / pip
    sp_med, sp_p95, sp_p99, sp_max = (float(sp.median()), float(sp.quantile(0.95)),
                                    float(sp.quantile(0.99)), float(sp.max()))
    if sym in FX and sp_med > 5:
        w.append(f"SPREAD_MED_ANORMAL:{sp_med:.1f}p")

    # F. microstructure
    gap = ts.diff().drop_nulls()
    g_p99, g_max = float(gap.quantile(0.99)), int(gap.max())
    # plus longue run de quotes figees (bid ET ask identiques au tick precedent)
    chg = ((bid != bid.shift()) | (ask != ask.shift())).fill_null(True)
    grp = chg.cum_sum()
    cnts = grp.value_counts()
    stale_max = int(cnts["count"].max() - 1) if cnts.height else 0
    av = df["askVolume"]
    vol_na = int((av.is_null() | (av <= 0)).sum())
    if vol_na > n * 0.5:
        w.append(f"VOLUMES_ABSENTS:{vol_na}")

    return {"symbol": sym, "file": f.name, "rows": n, "bytes": size,
            "ts_first": t0, "ts_last": t1, "non_mono": non_mono, "dup_ts": dup,
            "ms_frac": round(ms_used / n, 4), "ts_hors_mois": hors,
            "couverture_h": round(cov, 4), "jours_ouvres_manquants": missing,
            "min_ticks_jour": min_day,
            "med_ticks_jour": round(med_day), "bid_gt_ask": v_bad,
            "prix_med": med, "spread_med_pips": round(sp_med, 2),
            "spread_p95_pips": round(sp_p95, 2), "spread_p99_pips": round(sp_p99, 2),
            "spread_max_pips": round(sp_max, 2), "gap_p99_ms": round(g_p99),
            "gap_max_ms": g_max, "stale_run_max": stale_max,
            "vol_absents": vol_na}, w


def main():
    syms = sys.argv[1:] or sorted(p.name for p in DATA.iterdir() if p.is_dir())
    lines, warns_all = [], []
    for sym in syms:
        d = DATA / sym
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*-tick-*.csv")):
            t = time.time()
            st, w = audit_file(sym, f)
            st["warn"] = w
            st["audit_s"] = round(time.time() - t, 1)
            lines.append(st)
            warns_all += [f"{f.name}:{x}" for x in w]
            print(f"[{sym}] {f.name} rows={st['rows']} cov={st.get('couverture_h')} "
                  f"warn={w}", flush=True)
    OUT_JSONL.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    ok = [x for x in lines if not x["warn"]]
    with OUT_MD.open("w") as fh:
        fh.write(f"# Audit qualite tick -- {datetime.now(timezone.utc):%F %T} UTC\n\n")
        fh.write(f"Fichiers audites: {len(lines)} | sans warning: {len(ok)} "
                 f"| avec warnings: {len(lines) - len(ok)}\n\n")
        for x in lines:
            fh.write(f"## {x['symbol']} {x['file']}\n")
            fh.write(f"```json\n{json.dumps(x, indent=1)}\n```\n")
        fh.write("\n## Warnings consolides\n")
        for x in warns_all:
            fh.write(f"- {x}\n")
    print(f"\n=== {len(ok)}/{len(lines)} fichiers sans warning -> {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
