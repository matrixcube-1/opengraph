#!/usr/bin/env python3
"""ticks_to_poc.py — journal ticks ctrader -> data POC -> scan -> cand.npz

Le client enregistre chaque quote reconstruite (journal/ticks/*.jsonl) :
    {"ev":"tick","e":22,"bid":91.472,"ask":91.493,"ts":1793016000123}
`e` = indice FX (0..27, ordre poc_engine.FX — identique a universe::FX Rust).

Usage :
  convert TICKS_DIR OUT_DIR          -> OUT_DIR/<SYM>/<YYYY-MM-DD>/<HH>.csv
                                       (format exact de poc_engine.load_sym)
  scan    OUT_DIR DAY [DAY...]       -> Engine + scan -> OUT_DIR/cand_<DAY>.npz
  (DAY = YYYY-MM-DD ; plusieurs jours = plusieurs passes)

Volumes : le flux spot cTrader n'a pas de profondeur L1 -> avol=bvol=0
(le modele de fills repose sur move vs tau_slip, pas sur le volume).
"""
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import poc_engine as pe                       # noqa: E402

HDR = "ms,ask,bid,avol,bvol\n"


def convert(ticks_dir, out_dir):
    """jsonl ticks -> CSV horaires compatibles load_sym."""
    out = Path(out_dir)
    buckets = defaultdict(list)               # (sym, day, hour) -> [(ms,ask,bid)]
    n = 0
    for f in sorted(Path(ticks_dir).glob("*.jsonl")):
        for line in open(f):
            if '"tick"' not in line:
                continue
            ev = json.loads(line)
            if ev.get("ev") != "tick":
                continue
            sym = pe.FX[ev["e"]]
            t = int(ev["ts"])
            # bucketing UTC : la formule EPOCH du POC (mktime - timezone)
            # vaut le minuit UTC de la date, quel que soit le fuseau de
            # la machine — jour + heure doivent donc etre UTC.
            lt = time.gmtime(t / 1000)
            day = time.strftime("%Y-%m-%d", lt)
            buckets[(sym, day, lt.tm_hour)].append((t, ev["ask"], ev["bid"]))
            n += 1
    files = 0
    for (sym, day, h), rows in sorted(buckets.items()):
        rows.sort()
        t0 = int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000) - time.timezone * 1000
        d = out / sym / day
        d.mkdir(parents=True, exist_ok=True)
        # t0 du CSV = epoch UTC du jour — mktime+timezone reproduit EPOCH du POC
        t0_utc = t0
        with open(d / f"{h:02d}.csv", "w", newline="") as fh:
            fh.write(HDR)
            w = csv.writer(fh)
            for t, a, b in rows:
                w.writerow((t - (t0_utc + h * 3_600_000), a, b, 0.0, 0.0))
        files += 1
    print(f"convert: {n:,} ticks -> {files} fichiers horaires dans {out}")


def scan(out_dir, days):
    """Charge les CSV live via les constantes patchees -> scan -> npz."""
    pe.DATA = Path(out_dir)
    pe.DAYS = list(days)
    pe.EPOCH = {d: int(time.mktime(time.strptime(d, "%Y-%m-%d")) * 1000)
                - time.timezone * 1000 for d in pe.DAYS}
    pe.MED_CUTOFF = pe.EPOCH[pe.DAYS[-1]]      # mediane in-sample seule
    eng = pe.Engine(syms=pe.FX, mixed=False)
    cands, eps = eng.scan(out=None)
    print(f"scan {pe.DAYS}: {len(cands):,} candidats, {len(eps):,} episodes")
    np.savez(Path(out_dir) / f"cand_{'-'.join(pe.DAYS)}.npz",
             cands=np.array(cands), episodes=np.array(eps))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "convert":
        convert(sys.argv[2], sys.argv[3])
    elif cmd == "scan":
        scan(sys.argv[2], sys.argv[3:])
    else:
        sys.exit(__doc__)
