import csv, glob, os, collections

DATA = "/opt/arb/data"
DAYS = ["2026-10-22", "2026-10-23", "2026-10-26"]

print(f"{'sym':14s} {'files':>5s} {'ticks':>10s} {'couverture h/jour':>18s}  structure")
for sym in sorted(os.listdir(DATA)):
    sdir = os.path.join(DATA, sym)
    if not os.path.isdir(sdir):
        continue
    nfiles = nticks = 0
    hours_by_day = {d: set() for d in DAYS}
    gaps = []                      # (jour, heure_debut, heure_fin) zones vides
    for d in DAYS:
        ddir = os.path.join(sdir, d)
        for h in range(24):
            f = os.path.join(ddir, f"{h:02d}.csv")
            if os.path.exists(f):
                nfiles += 1
                hours_by_day[d].add(h)
                with open(f) as fh:
                    nticks += max(sum(1 for _ in fh) - 1, 0)
    # trous horaires par jour
    hole_str = []
    for d in DAYS:
        hs = hours_by_day[d]
        if not hs:
            hole_str.append(f"{d}:VIDE")
            continue
        missing = [h for h in range(min(hs), max(hs) + 1) if h not in hs]
        cov = f"{len(hs)}/24h"
        if missing:
            hole_str.append(f"{d[-5:]}:{cov} trous={missing[:8]}")
        else:
            first, last = min(hs), max(hs)
            hole_str.append(f"{d[-5:]}:{cov} [{first:02d}-{last:02d}]")
    print(f"{sym:14s} {nfiles:>5d} {nticks:>10,d} {'':>18s}  {' | '.join(hole_str)}")
