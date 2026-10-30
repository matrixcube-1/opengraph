#!/usr/bin/env python3
"""Orchestrateur ticks Dukascopy v3 — file unique, 1 requete/seconde.

Usage: pull_all.py   (un seul processus: verrou exclusif sur pull.lock)
Telecharge (symbole, mois) un par un, sequentiellement, avec dukascopy-node
en lots de 1 requete suivis d'une pause de 1000 ms. Au premier signe de
blocage (403/429/WAF) tout s'arrete BREAKER_S secondes, puis reprend sur
le meme mois; ce blocage n'est pas compte comme un echec du mois.
Univers = Annexe J du document (28 FX + 32 bouts).
"""
import fcntl
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

BASE = Path("/opt/arb/data")
LEDGER = BASE / "ledger.json"
LOCK = BASE / "pull.lock"
LOG = Path("/opt/arb/logs")
START = date(2025, 1, 1)   # fenetre: 2025-01-01 -> 2026-09-01 (exclu)
END = date(2026, 8, 31)    # dernier mois inclus: aout 2026
CLAIM_TTL = 3 * 3600  # s
MAX_FAIL = 10
BREAKER_S = 2 * 3600
BLOCK_RE = re.compile(r"\b(403|429)\b|WAF token|Too Many Requests|rate.?limit", re.I)

MANIFEST = [
    ("EURUSD","eurusd",0),("GBPUSD","gbpusd",0),("USDJPY","usdjpy",0),
    ("USDCHF","usdchf",0),("AUDUSD","audusd",0),("USDCAD","usdcad",0),
    ("NZDUSD","nzdusd",0),("EURGBP","eurgbp",0),("EURJPY","eurjpy",0),
    ("EURCHF","eurchf",1),("EURAUD","euraud",1),("EURCAD","eurcad",1),
    ("EURNZD","eurnzd",1),("GBPJPY","gbpjpy",0),("GBPCHF","gbpchf",1),
    ("GBPAUD","gbpaud",1),("GBPCAD","gbpcad",1),("GBPNZD","gbpnzd",1),
    ("AUDJPY","audjpy",0),("AUDCHF","audchf",1),("AUDCAD","audcad",1),
    ("AUDNZD","audnzd",1),("CADJPY","cadjpy",1),("CADCHF","cadchf",1),
    ("CHFJPY","chfjpy",1),("NZDJPY","nzdjpy",1),("NZDCHF","nzdchf",1),
    ("NZDCAD","nzdcad",1),
    ("DE40","deuidxeur",0),("IT40","itaidxeur",1),("F40","fraidxeur",1),
    ("STOXX50","eusidxeur",1),("NETH25","nldidxeur",1),("XAUEUR","xaueur",1),
    ("XAGEUR","xageur",1),("UK100","gbridxgbp",0),("XAUGBP","xaugbp",1),
    ("JP225","jpnidxjpy",0),("XAUJPY","xaujpy",1),("XAUCHF","xauchf",1),
    ("AUS200","ausidxaud",1),("XAUAUD","xauaud",1),("XAGAUD","xagaud",1),
    ("US30","usa30idxusd",0),("US500","usa500idxusd",0),
    ("US2000","ussc2000idxusd",1),("USTEC","usatechidxusd",0),
    ("SWI20","cheidxchf",1),("XAUUSD","xauusd",0),("XAGUSD","xagusd",1),
    ("XTIUSD","lightcmdusd",0),("XBRUSD","brentcmdusd",0),
    ("XPTUSD","xptcmdusd",1),("XPDUSD","xpdcmdusd",1),
    ("SUGAR","sugarcmdusd",1),("COCOA","cocoacmdusd",1),
    ("COFFEE","coffeecmdusx",1),("COTTON","cottoncmdusx",1),
    ("SOYBEAN","soybeancmdusx",1),("OJ","ojuicecmdusx",1),
]


def month_range(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def locked_ledger(fn):
    """Ouvre ledger.json sous flock exclusif, applique fn(d)->result, sauvegarde."""
    LEDGER.touch(exist_ok=True)
    with open(LEDGER, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            d = json.load(f) or {}
        except json.JSONDecodeError:
            d = {}
        res = fn(d)
        f.seek(0)
        f.truncate()
        json.dump(d, f)
        f.flush()
    return res


def claim():
    """Retourne (ic, sym, key, y, m) du prochain item a faire, ou None."""
    now = time.time()
    todo = None

    def pick(d):
        nonlocal todo
        for ic, sym, _ in MANIFEST:
            for y, m in month_range(START, END):
                key = f"{y}-{m:02d}"
                st = d.get(ic, {}).get(key)
                if st in ("ok", "empty"):
                    continue
                if isinstance(st, dict) and st.get("state") == "fail":
                    if st.get("n", 0) >= MAX_FAIL:
                        continue  # bloque durablement -> revue manuelle
                    if now - st.get("ts", 0) < 60:
                        continue  # cooldown anti-boucle
                todo = (ic, sym, key, y, m)
                prev_n = st.get("n", 0) if isinstance(st, dict) else 0
                d.setdefault(ic, {})[key] = {"claim": 0, "ts": now, "n": prev_n}
                return
    locked_ledger(pick)
    return todo


def finish(ic, key, state):
    """ok/empty -> definitif; fail -> +1 essai; blocked -> rien de compte."""
    def setit(d):
        prev = d.get(ic, {}).get(key)
        n = prev.get("n", 0) if isinstance(prev, dict) else 0
        if state == "fail":
            d.setdefault(ic, {})[key] = {"state": "fail", "n": n + 1, "ts": time.time()}
        elif state == "blocked":
            d.setdefault(ic, {})[key] = {"state": "pending", "n": n, "ts": 0}
        else:
            d.setdefault(ic, {})[key] = state
    locked_ledger(setit)


def pull(sym, y, m, outdir):
    dfrom = f"{y}-{m:02d}-01"
    # -to est exclusif chez dukascopy-node : borne = 1er jour du mois suivant
    dto = f"{y + (m == 12)}-{m % 12 + 1:02d}-01"
    cmd = [
        "dukascopy-node", "-i", sym, "-from", dfrom, "-to", dto,
        "-t", "tick", "-f", "csv", "-dir", str(outdir), "-v", "-s",
        "-bs", "1", "-bp", "1000",          # 1 requete puis 1 s de pause
        "-r", "3", "-rp", "5000", "--waf-timeout", "120000",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)


def main():
    LOG.mkdir(parents=True, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("pull_all.py tourne deja: un seul processus autorise")
    log = open(LOG / "pull.log", "a", buffering=1)
    while True:
        item = claim()
        if item is None:
            print(f"{time.strftime('%F %T')} CAMPAGNE_TERMINEE", file=log)
            return
        ic, sym, key, y, m = item
        outdir = BASE / "ticks" / ic
        outdir.mkdir(parents=True, exist_ok=True)
        # telechargement en zone temporaire: l'ancien fichier n'est remplace
        # qu'une fois le nouveau complet
        tmpdir = BASE / "tmp" / ic / key
        shutil.rmtree(tmpdir, ignore_errors=True)
        tmpdir.mkdir(parents=True)
        t0 = time.time()
        nbytes, detail = 0, ""
        try:
            r = pull(sym, y, m, tmpdir)
        except subprocess.TimeoutExpired:
            state = "fail"
            detail = "timeout"
        else:
            out = (r.stdout or "") + (r.stderr or "")
            csvs = list(tmpdir.glob(f"{sym}-tick-{key}*.csv"))
            nbytes = sum(f.stat().st_size for f in csvs)
            if r.returncode != 0:
                state = "blocked" if BLOCK_RE.search(out) else "fail"
                detail = out.strip().splitlines()[-1][:200] if out.strip() else ""
            elif nbytes > 0:
                state = "ok"
                for old in outdir.glob(f"{sym}-tick-{key}*.csv"):
                    old.unlink()
                for f in csvs:
                    f.replace(outdir / f.name)
            else:
                state = "empty"  # outil OK, aucun tick = mois legit vide
        shutil.rmtree(tmpdir, ignore_errors=True)
        finish(ic, key, state)
        print(f"{time.strftime('%F %T')} {ic} {key} {state} {nbytes}B "
              f"{time.time()-t0:.0f}s {detail}", file=log)
        if state == "blocked":
            print(f"{time.strftime('%F %T')} BLOCAGE detecte -> pause {BREAKER_S}s", file=log)
            time.sleep(BREAKER_S)


if __name__ == "__main__":
    main()
