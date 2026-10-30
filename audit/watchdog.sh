#!/bin/bash
# Watchdog qualite : toutes les 30 min, re-verifie tous les CSV termines
# et remet les mois suspects en file via le ledger.
while true; do
  echo "=== $(date '+%F %T') ===" >> /opt/arb/logs/verify.log
  /opt/arb/venv/bin/python /opt/arb/src/verify_coverage.py --requeue >> /opt/arb/logs/verify.log 2>&1
  sleep 1800
done
