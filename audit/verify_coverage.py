#!/usr/bin/env python3
"""Verification d'integrite des mois telecharges (post-campagne ou periodique).

Pour chaque CSV symbole-mois:
  - header + lignes parsables ts,ask,bid,askVol,bidVol
  - timestamps monotones croissants
  - 0 < bid <= ask (ecarts toleres: bid==ask autorise)
  - couverture horaire: nb d'heures distinctes avec >=1 tick
  - mois suspect si couverture < 30% de la MEDIANE des mois du symbole
    ou si anomalies de parsing
Sortie: rapport + option --requeue qui remet les mois suspects en 'fail'
dans le ledger pour re-pull par les workers.
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

BASE = Path("/opt/arb/data")
LEDGER = BASE / "ledger.json"
REQUEUE = BASE / "requeue.json"
CACHE = BASE / "verify_cache.json"
MAX_REQUEUE = 2
CSV_GLOB = "*-tick-*.csv"


def missing_weekdays(month, days):
    """Jours lun-ven du mois 'YYYY-MM' sans aucun tick (days = set de jours epoch)."""
    y, m = int(month[:4]), int(month[5:7])
    t, out = date(y, m, 1), []
    while t.month == m:
        if t.weekday() < 5 and (t - date(1970, 1, 1)).days not in days:
            out.append(t.strftime("%m-%d"))
        t += timedelta(days=1)
    return out


def parse_month(fpath):
    """Retourne (n_ticks, n_bad_rows, n_heures, monotones_ok, jours)."""
    n = bad = 0
    hours = set()
    days = set()
    prev_ts = -1
    mono = True
    with open(fpath) as f:
        header = f.readline()
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 5:
                bad += 1
                continue
            try:
                ts = int(parts[0]); ask = float(parts[1]); bid = float(parts[2])
            except ValueError:
                bad += 1
                continue
            if not (0 < bid <= ask):
                bad += 1
                continue
            n += 1
            hours.add(ts // 3_600_000)
            days.add(ts // 86_400_000)
            if ts < prev_ts:  # meme milliseconde autorisee
                mono = False
            prev_ts = ts
    return n, bad, len(hours), mono, days


def main():
    requeue = "--requeue" in sys.argv
    stats = defaultdict(dict)   # sym -> month -> metrics
    suspects = []
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    for symdir in sorted(BASE.glob("ticks/*")):
        sym = symdir.name
        for f in symdir.glob(CSV_GLOB):
            if ".part" in f.name:
                continue
            # mois = 2e segment du nom: sym-tick-YYYY-MM-DD-YYYY-MM-DD.csv
            try:
                month = f.name.split("-tick-")[1][:7]
            except IndexError:
                continue
            st = f.stat()
            ck = f"{f}:{st.st_size}:{int(st.st_mtime)}"
            if ck not in cache:
                n, bad, nh, mono, days = parse_month(f)
                cache[ck] = dict(n=n, bad=bad, h=nh, mono=mono, sz=st.st_size,
                                 miss=missing_weekdays(month, days))
            stats[sym][month] = cache[ck]
    CACHE.write_text(json.dumps(cache))

    for sym, months in stats.items():
        hs = sorted(m["h"] for m in months.values() if m["n"] > 0)
        med = hs[len(hs) // 2] if hs else 0
        for month, m in sorted(months.items()):
            suspect = ((m["n"] == 0) or m["bad"] > 0 or not m["mono"] or m["miss"]
                       or (med > 0 and m["h"] < 0.3 * med))
            if suspect:
                suspects.append((sym, month, m))

    print(f"{len(stats)} symboles, {sum(len(v) for v in stats.values())} mois, {len(suspects)} suspects")
    for sym, month, m in suspects:
        print(f"  SUSPECT {sym} {month} ticks={m['n']} bad={m['bad']} h={m['h']} "
              f"mono={m['mono']} jours_manquants={m['miss']} sz={m['sz']}")

    if requeue and suspects:
        # chaque mois suspect n'est remis en file que MAX_REQUEUE fois;
        # au-dela il reste en l'etat et part en revue manuelle
        import fcntl
        rq = json.loads(REQUEUE.read_text()) if REQUEUE.exists() else {}
        with open(LEDGER, "r+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            d = json.load(lf) or {}
            for sym, month, _ in suspects:
                k = f"{sym}/{month}"
                if d.get(sym, {}).get(month) != "ok":
                    continue  # deja en cours ou en file
                if rq.get(k, 0) >= MAX_REQUEUE:
                    print(f"  REVUE_MANUELLE {k} (deja remis {rq[k]} fois)")
                    continue
                rq[k] = rq.get(k, 0) + 1
                d.setdefault(sym, {})[month] = {"state": "pending", "n": 0, "ts": 0}
                print(f"  REMIS_EN_FILE {k} ({rq[k]}/{MAX_REQUEUE})")
            lf.seek(0); lf.truncate(); json.dump(d, lf)
        REQUEUE.write_text(json.dumps(rq))


if __name__ == "__main__":
    main()
