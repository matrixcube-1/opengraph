#!/usr/bin/env python3
"""Tests de ticks_to_poc.convert — le maillon journal -> POC.

Couvre : format CSV exact (load_sym), bucketing UTC (jour + heure),
tri intra-fichier, volumes nuls, et robustesse aux lignes corrompues
(un seul tick mauvais ne doit PAS perdre la conversion — le journal
peut contenir des lignes d'autres événements ou tronquées).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import poc_engine as pe          # noqa: E402
import ticks_to_poc as t2p       # noqa: E402

# vendredi 2026-10-23, 13:00:00.000 UTC exact
D = "2026-10-23"
H13 = 1792760400000  # epoch UTC verifie : datetime(2026,10,23,13,tzinfo=utc)


def _w(f, lines):
    with open(f, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def _csv_rows(p):
    rows = Path(p).read_text().strip().split("\n")
    assert rows[0] == "ms,ask,bid,avol,bvol", f"header: {rows[0]}"
    return [tuple(float(x) for x in r.split(",")) for r in rows[1:]]


def test_format_et_tri():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
        _w(Path(td) / "a.jsonl", [
            # desordre volontaire : le tri par ts est attendu
            json.dumps({"ev": "tick", "e": 0, "bid": 1.10, "ask": 1.1002,
                        "ts": H13 + 2000}),
            json.dumps({"ev": "tick", "e": 0, "bid": 1.10, "ask": 1.1001,
                        "ts": H13 + 500}),
            json.dumps({"ev": "opportunity", "t0": 1, "pi": 0}),   # ignore
            json.dumps({"ev": "tick", "e": 22, "bid": 91.4, "ask": 91.45,
                        "ts": H13 + 100}),
        ])
        t2p.convert(td, od)
        r0 = _csv_rows(Path(od) / "EURUSD" / D / "13.csv")
        assert r0 == [(500.0, 1.1001, 1.10, 0.0, 0.0),
                      (2000.0, 1.1002, 1.10, 0.0, 0.0)], r0
        r22 = _csv_rows(Path(od) / "CADJPY" / D / "13.csv")
        assert r22 == [(100.0, 91.45, 91.4, 0.0, 0.0)], r22
    print("  format + tri + filtre ev                    OK")


def test_bucketing_utc():
    """Un tick a 23:59:59.999 et un a 00:00:00.001 -> deux jours distincts ;
    un tick a 13:59:59 et un a 14:00:00 -> deux fichiers horaires."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
        day0 = H13 - (H13 % 86400000)          # minuit UTC du jour
        _w(Path(td) / "a.jsonl", [
            json.dumps({"ev": "tick", "e": 0, "bid": 1, "ask": 1.01,
                        "ts": day0 + 86399999}),           # 23:59:59.999
            json.dumps({"ev": "tick", "e": 0, "bid": 1, "ask": 1.01,
                        "ts": day0 + 86400001}),           # +jour, 00:00:00.001
            json.dumps({"ev": "tick", "e": 0, "bid": 1, "ask": 1.01,
                        "ts": H13 - 1000}),                # 12:59:59 -> 12.csv
        ])
        t2p.convert(td, od)
        from datetime import datetime, timezone, timedelta
        d0 = datetime.fromtimestamp(day0 / 1000, timezone.utc)
        d1 = (d0 + timedelta(days=1)).strftime("%Y-%m-%d")
        d0s = d0.strftime("%Y-%m-%d")
        assert _csv_rows(Path(od) / "EURUSD" / d0s / "23.csv") == \
            [(86399999 - 23 * 3600000.0, 1.01, 1.0, 0.0, 0.0)]
        assert _csv_rows(Path(od) / "EURUSD" / d1 / "00.csv") == \
            [(1.0, 1.01, 1.0, 0.0, 0.0)]
        assert _csv_rows(Path(od) / "EURUSD" / d0s / "12.csv") == \
            [(H13 - 1000 - (day0 + 12 * 3600000), 1.01, 1.0, 0.0, 0.0)]
    print("  bucketing UTC jour + heure                  OK")


def test_lignes_corrompues_ignorees():
    """JSON casse, e hors 0..27, champs manquants -> ignorer, pas planter."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
        _w(Path(td) / "a.jsonl", [
            "{not json",
            json.dumps({"ev": "tick", "e": 99, "bid": 1, "ask": 1, "ts": H13}),
            json.dumps({"ev": "tick", "e": -1, "bid": 1, "ask": 1, "ts": H13}),
            json.dumps({"ev": "tick", "e": 0, "bid": 1.1}),          # ask/ts manquants
            json.dumps({"ev": "tick", "e": 0, "bid": 1.1, "ask": 1.11, "ts": H13}),
            "",                                                    # ligne vide
        ])
        t2p.convert(td, od)
        r = _csv_rows(Path(od) / "EURUSD" / D / "13.csv")
        assert r == [(0.0, 1.11, 1.1, 0.0, 0.0)], r
        # e:-1 ne doit PAS passer par l'indexation negative Python
        assert not (Path(od) / "NZDCHF").exists(), "e:-1 fuit via FX[-1]"
        assert len(list(Path(od).rglob("*.csv"))) == 1
    print("  lignes corrompues ignorees (pas de crash)   OK")


def test_format_identique_cbot():
    """Le cBot C# ecrit le meme schema : roundtrip bout en bout."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as od:
        _w(Path(td) / "2026-10-23.jsonl", [
            # exactement la ligne que produit OgDetect.cs LogTick
            '{"ev":"tick","e":22,"bid":91.472,"ask":91.493,"ts":%d}' % H13,
        ])
        t2p.convert(td, od)
        assert pe.FX[22] == "CADJPY"
        r = _csv_rows(Path(od) / "CADJPY" / D / "13.csv")
        assert r == [(0.0, 91.493, 91.472, 0.0, 0.0)], r
    print("  schema cBot -> CSV                          OK")


if __name__ == "__main__":
    test_format_et_tri()
    test_bucketing_utc()
    test_lignes_corrompues_ignorees()
    test_format_identique_cbot()
    print("test_ticks_to_poc : 4/4 OK")
