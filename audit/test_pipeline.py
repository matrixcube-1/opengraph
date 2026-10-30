#!/usr/bin/env python3
"""Tests hors-ligne de pull_all.py et verify_coverage.py (aucune requete reseau).

Usage: python test_pipeline.py <dossier contenant pull_all.py et verify_coverage.py>
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else ".")


def load(name, base):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = base
    mod.LEDGER = base / "ledger.json"
    for attr, fname in (("LOCK", "pull.lock"), ("REQUEUE", "requeue.json"),
                        ("CACHE", "verify_cache.json")):
        if hasattr(mod, attr):
            setattr(mod, attr, base / fname)
    if hasattr(mod, "LOG"):
        mod.LOG = base / "logs"
    return mod


def write_month(path, y, m, skip_days=()):
    """CSV synthetique: 1 tick par heure, lun-ven, sauf skip_days."""
    rows = ["timestamp,askPrice,bidPrice,askVolume,bidVolume"]
    t = datetime(y, m, 1, tzinfo=timezone.utc)
    while t.month == m:
        if t.weekday() < 5 and t.day not in skip_days:
            for h in range(24):
                ts = int((t + timedelta(hours=h)).timestamp() * 1000) + 123
                rows.append(f"{ts},1.1002,1.1000,1.5,1.2")
        t += timedelta(days=1)
    path.write_text("\n".join(rows) + "\n")


ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1
    print("OK  ", msg)


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    p = load("pull_all", base)
    p.MANIFEST = [("EURUSD", "eurusd", 0)]
    p.START, p.END = date(2025, 1, 1), date(2025, 3, 31)
    p.BREAKER_S = 0
    p.LOG.mkdir(parents=True)

    # 1. bornes de dates (-to exclusif)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        outdir = Path(cmd[cmd.index("-dir") + 1])
        y, m = map(int, cmd[cmd.index("-from") + 1].split("-")[:2])
        key = f"{y}-{m:02d}"
        scenario = SCEN.pop(0)
        if scenario == "ok":
            write_month(outdir / f"eurusd-tick-{key}-01-x.csv", y, m)
            return types.SimpleNamespace(returncode=0, stdout="File saved", stderr="")
        if scenario == "blocked":
            return types.SimpleNamespace(returncode=1, stdout="",
                stderr="Headless browser could not acquire a WAF token (last HTTP status: 403)")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="artifact failed")

    p.subprocess.run = fake_run
    # scenario: jan blocked puis ok, fev fail puis ok, mars ok
    SCEN = ["blocked", "ok", "fail", "ok", "ok"]
    real_claim = p.claim
    # neutralise le cooldown de 60 s pour le test
    p.time.time = (lambda t0=[1e9]: (t0.__setitem__(0, t0[0] + 100), t0[0])[1])
    p.main()

    froms = [(c[c.index("-from") + 1], c[c.index("-to") + 1]) for c in calls]
    check(froms[0] == ("2025-01-01", "2025-02-01"), f"borne -to exclusive: {froms[0]}")
    check(all("-bs" in c and c[c.index("-bs") + 1] == "1" and c[c.index("-bp") + 1] == "1000"
              for c in calls), "1 requete par lot + pause 1000 ms")
    led = json.loads((base / "ledger.json").read_text())["EURUSD"]
    check(led == {"2025-01": "ok", "2025-02": "ok", "2025-03": "ok"}, f"ledger final: {led}")
    check(len(calls) == 5, f"5 appels (blocage rejoue, echec rejoue): {len(calls)}")
    log = (base / "logs" / "pull.log").read_text()
    check("BLOCAGE detecte" in log, "blocage 403 detecte et pause declenchee")
    check(not list((base / "tmp").rglob("*.csv")), "zone temporaire nettoyee")
    check(len(list((base / "ticks" / "EURUSD").glob("*.csv"))) == 3, "3 fichiers finaux")

    # 2. blocage ne consomme pas d'essai ; echec si
    import fcntl  # noqa: F401  (verifie qu'on est bien sous Linux)

    # 3. verrou: un 2e processus est refuse
    lk = open(p.LOCK, "w")
    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        p.main()
        check(False, "2e processus aurait du etre refuse")
    except SystemExit as e:
        check("un seul processus" in str(e), "2e processus refuse par le verrou")
    lk.close()

    # 4. verify_coverage: jour ouvre manquant -> suspect + remis en file, plafonne
    v = load("verify_coverage", base)
    d = base / "ticks" / "EURUSD"
    write_month(d / "eurusd-tick-2025-03-01-x.csv", 2025, 3, skip_days=(31,))
    (base / "ledger.json").write_text(json.dumps({"EURUSD": {
        "2025-01": "ok", "2025-02": "ok", "2025-03": "ok"}}))
    sys.argv = ["v", "--requeue"]
    v.main()
    led = json.loads((base / "ledger.json").read_text())["EURUSD"]
    check(led["2025-03"] == {"state": "pending", "n": 0, "ts": 0}, "mars (31 manquant) remis en file")
    check(led["2025-01"] == "ok" and led["2025-02"] == "ok", "mois complets intacts")
    check(v.missing_weekdays("2025-03", set()) [-1] == "03-31", "detection jour ouvre 31/03")
    for _ in range(3):
        led["2025-03"] = "ok"
        (base / "ledger.json").write_text(json.dumps({"EURUSD": led}))
        v.main()
    rq = json.loads((base / "requeue.json").read_text())
    check(rq["EURUSD/2025-03"] == 2, f"remise en file plafonnee a 2: {rq}")
    check(len(json.loads((base / "verify_cache.json").read_text())) >= 3, "cache de verification ecrit")

print(f"\n{ok} verifications reussies")
