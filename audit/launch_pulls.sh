#!/bin/bash
# Lance N workers pull_all.py en arriere-plan, journal par worker.
N=${1:-4}
cd /opt/arb/src
for i in $(seq 0 $((N-1))); do
  nohup /opt/arb/venv/bin/python pull_all.py $i $N >> /opt/arb/logs/pull_${i}.out 2>&1 &
  echo "worker $i pid $!"
done
