#!/bin/bash
cd /opt/arb/poc
PY=/opt/arb/venv-og/bin/python
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.1 --kq 0.5 --step 20 --out run_d01_05_s20.json > nr_d1b.log 2>&1
echo D01x05 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.1 --kq 1.0 --step 20 --out run_d01_10_s20.json > nr_d2b.log 2>&1
echo D01x10 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.0 --kq 0.5 --step 20 --out run_d00_05_s20.json > nr_d3b.log 2>&1
echo D00x05 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.0 --kq 1.0 --step 20 --out run_d00_10_s20.json > nr_d4b.log 2>&1
echo D00x10 done $(date -u +%T)
echo ALL-DONE $(date -u +%T)
