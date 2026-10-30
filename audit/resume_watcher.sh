#!/bin/bash
# Sonde la WAF toutes les 30 min; des qu'un pull minuscule passe, relance 4 workers.
while true; do
  sleep 1800
  if timeout 150 dukascopy-node -i eurchf -from 2025-01-06 -to 2025-01-07 -t tick -f csv -dir /tmp/probe -v -s -r 2 2>&1 | grep -q "File saved"; then
    echo "$(date '+%F %T') probe OK -> relance 4 workers" >> /opt/arb/logs/resume.log
    rm -rf /tmp/probe
    cd /opt/arb/src
    i=0; while [ $i -lt 4 ]; do
      nohup /opt/arb/venv/bin/python pull_all.py $i >> /opt/arb/logs/pull_$i.out 2>&1 &
      i=$((i+1))
    done
    exit 0
  fi
  echo "$(date '+%F %T') probe refusee" >> /opt/arb/logs/resume.log
done
