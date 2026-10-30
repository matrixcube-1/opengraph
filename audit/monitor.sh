#!/bin/bash
# Monitoring campagne : CPU, RAM, disque, progression ledger — toutes les 60 s.
# Usage: nohup monitor.sh &
while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  cpu=$(top -bn1 | grep '%Cpu' | awk '{print 100-$8}')
  mem=$(free -m | awk '/Mem:/ {printf "%d/%dMB (%.0f%%)", $3, $2, $3*100/$2}')
  dsk=$(df -h / | tail -1 | awk '{print $3"/"$2" ("$5")"}')
  led=$(python3 -c "import json;d=json.load(open('/opt/arb/data/ledger.json'));print(sum(1 for v in d.values() for s in v.values() if s=='ok'),'mois ok /',sum(len(v) for v in d.values()),'tentes')" 2>/dev/null || echo 'ledger absent')
  wrk=$(pgrep -fc 'pull_all.py')
  last=$(tail -1 /opt/arb/logs/pull.log 2>/dev/null | cut -c1-120)
  echo "$ts | cpu=${cpu}% | mem=$mem | disk=$dsk | workers=$wrk | $led | dernier: $last" >> /opt/arb/logs/monitor.log
  sleep 60
done
