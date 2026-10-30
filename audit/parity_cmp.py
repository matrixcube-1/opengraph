#!/usr/bin/env python3
"""Comparateur de parite Python<->Rust avec tolerance explicite.

Compare parity_ref.json (POC Python) au JSON emis par l'exemple Rust
`parity` (`cargo run --example parity -- <fixture>` > rust_out.json).

Tolerance : REL_TOL = 1e-9 en relatif sur CHAQUE champ numerique —
couvre les differences de contraction FMA entre compilateurs/cibles
sans masquer de vraie divergence. Identite stricte sur legs, start,
res, fills, parts (le `pi` global differe : l'enum Rust n'intercale pas
les variantes bouts ; la correspondance se fait via (legs, start)).

Usage: python parity_cmp.py parity_ref.json rust_out.json
"""
import json
import sys

REL_TOL = 1e-9

ref = json.load(open(sys.argv[1]))
rust = json.load(open(sys.argv[2]))


def close(a, b, tag):
    if a == b:
        return True
    rel = abs(a - b) / max(abs(a), abs(b), 1e-12)
    ok = rel <= REL_TOL
    if not ok:
        print(f"  ECART {tag}: ref={a!r} rust={b!r} rel={rel:.2e}")
    return ok


fails = 0

# --- evals ---
re_evals = ref["evals"]
ru_evals = [e for e in rust["evals"] if "exec" not in e]
ru_execs = [e["exec"] for e in rust["evals"] if "exec" in e]
assert len(re_evals) == len(ru_evals), f"nb evals {len(re_evals)} vs {len(ru_evals)}"
for i, (re_, ru) in enumerate(zip(re_evals, ru_evals)):
    tag = f"eval[{i}]"
    if re_.get("none") or ru.get("none"):
        if not (re_.get("none") and ru.get("none")):
            print(f"  ECART {tag}: none mismatch ref={re_} rust={ru}")
            fails += 1
        continue
    ok = True
    ok &= close(re_["net"], ru["net"], tag + ".net")
    ok &= close(re_["ddd"], ru["ddd"], tag + ".ddd")
    ok &= re_["legs"] == ru["legs"]
    ok &= re_["start"] == ru["start"]
    if not ok:
        fails += 1
        print(f"  ref={re_} rust={ru}")

# --- execs ---
assert len(ref["execs"]) == len(ru_execs), f"nb execs {len(ref['execs'])} vs {len(ru_execs)}"
for i, (re_, ru) in enumerate(zip(ref["execs"], ru_execs)):
    tag = f"exec[{i}] t={re_['t']} lat={re_['lat']}"
    if re_["res"] is None or ru["res"] is None:
        if not (re_["res"] is None and ru["res"] is None):
            print(f"  ECART {tag}: None mismatch ref={re_} rust={ru}")
            fails += 1
        continue
    ok = True
    ok &= re_["res"] == ru["res"].split(":")[0]           # done / abort
    if re_["res"] == "abort":
        ok &= re_["why"] == ru["res"].split(":", 1)[1]    # raison d'abort
    ok &= close(re_["pnl"], ru["pnl"], tag + ".pnl")
    ok &= re_["fills"] == ru["fills"]
    ok &= re_["parts"] == ru["parts"]
    ok &= close(re_["dur"], ru["dur"], tag + ".dur")
    if not ok:
        fails += 1
        print(f"  ref={re_} rust={ru}")

n = len(re_evals) + len(ref["execs"])
print(f"{'PARITE OK' if fails == 0 else f'{fails} ECART(S)'} — {n} comparaisons, rel_tol={REL_TOL}")
sys.exit(0 if fails == 0 else 1)
