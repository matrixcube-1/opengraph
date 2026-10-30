#!/bin/bash
cd /opt/arb/poc
PY=/opt/arb/venv-og/bin/python
$PY nt_realism.py --mode internal --lat-ms 0 --vol-guard --step 20 --out ref_vg2_s20.json > nr_vg2.log 2>&1
echo VG2 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.1 --kq 0.5 --step 20 --out run_d01_05_s20.json > nr_d1.log 2>&1
echo D01x05 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.1 --kq 1.0 --step 20 --out run_d01_10_s20.json > nr_d2.log 2>&1
echo D01x10 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.0 --kq 0.5 --step 20 --out run_d00_05_s20.json > nr_d3.log 2>&1
echo D00x05 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.0 --kq 1.0 --step 20 --out run_d00_10_s20.json > nr_d4.log 2>&1
echo D00x10 done $(date -u +%T)
$PY nt_realism.py --mode real --lat-ms 5 --queue --hq-sp 0.1 --kq 0.0 --step 20 --out run_d01_00_s20.json > nr_d5.log 2>&1
echo D01x00 done $(date -u +%T)
echo ALL-DONE $(date -u +%T)
