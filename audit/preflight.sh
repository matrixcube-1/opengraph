#!/bin/bash
# Tests d'acces AVANT toute campagne. S'arrete au premier test bloquant.
# Volume total: ~30 requetes, un seul lancement de navigateur.
set -u
URL=https://jetta.dukascopy.com/v1/ticks/EUR-USD/2025/1/6/10
T=/tmp/preflight; rm -rf $T; mkdir -p $T
fail() { echo "PREFLIGHT ECHEC: $1"; exit 1; }

echo "== 0. IP sortante: $(curl -s -m 10 https://api.ipify.org)"

echo "== 1. reponse du flux (attendu: 202 + challenge, ou 200)"
H=$(curl -s -o /dev/null -D - -m 20 "$URL")
echo "$H" | grep -iE '^HTTP/|x-amzn-waf-action'
echo "$H" | grep -qE '^HTTP/[0-9.]+ (200|202)' || fail "le flux ne repond ni 200 ni 202"

echo "== 2. un jour EURUSD (jeton via navigateur + 24 requetes a 1/s)"
rm -f /root/.dukascopy-node/waf-token.json
timeout 300 dukascopy-node -i eurusd -from 2025-01-06 -to 2025-01-07 -t tick -f csv \
  -dir $T -v -s -bs 1 -bp 1000 -r 3 -rp 5000 --waf-timeout 120000 -d > $T/run.log 2>&1
RC=$?
grep -E 'Acquir|Saved WAF|403|429|could not' $T/run.log | head -5
F=$(ls $T/*.csv 2>/dev/null | head -1)
[ $RC -eq 0 ] && [ -n "$F" ] || fail "telechargement d'un jour impossible (rc=$RC)"
N=$(($(wc -l < "$F") - 1))
echo "   fichier: $(basename "$F")  ticks: $N"
[ "$N" -gt 10000 ] || fail "trop peu de ticks pour un lundi EURUSD ($N)"
/opt/arb/venv/bin/python - "$F" <<'EOF' || fail "contenu invalide"
import sys, polars as pl
df = pl.read_csv(sys.argv[1])
ts = df["timestamp"]
assert list(df.columns) == ["timestamp","askPrice","bidPrice","askVolume","bidVolume"], df.columns
assert (ts.diff().drop_nulls() >= 0).all(), "timestamps non monotones"
assert (df["bidPrice"] <= df["askPrice"]).all(), "bid > ask"
h = sorted(set((ts // 3_600_000 % 24).to_list()))
assert len(h) == 24, f"heures presentes: {h}"
print(f"   contenu OK: 24/24 heures, monotone, bid<=ask, ms={(ts % 1000 != 0).mean():.3f}")
EOF

echo "== 3. jeton reutilise sans navigateur (meme jour, cache)"
rm -rf $T/b; mkdir -p $T/b
timeout 120 dukascopy-node -i eurusd -from 2025-01-07 -to 2025-01-08 -t tick -f csv \
  -dir $T/b -v -s -bs 1 -bp 1000 -r 3 -rp 5000 -d > $T/run2.log 2>&1 || fail "2e jour impossible"
if grep -q 'Acquiring token' $T/run2.log; then echo "   ATTENTION: navigateur relance (jeton non reutilise)"; else echo "   OK: jeton en cache reutilise"; fi

echo "== 4. info: les heures du samedi sont-elles demandees ?"
grep -oE 'jetta[^ ]+' $T/run.log | head -2 || true

echo "PREFLIGHT OK"
