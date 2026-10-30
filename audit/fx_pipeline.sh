#!/bin/bash
# Watcher : attend les 28 paires FX, puis validation -> rescan (sequenciel)
# Le downloader continue seul sur les CFD en parallele (CPU vs reseau)
cd /opt/arb/poc
LOG=/tmp/fx_pipeline.log
echo "[$(date -u +%H:%M)] watcher demarre" >> $LOG

# 28 paires FX attendues
FX="EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD EURGBP EURJPY EURCHF EURAUD EURCAD EURNZD GBPJPY GBPCHF GBPAUD GBPCAD GBPNZD AUDJPY AUDCHF AUDCAD AUDNZD CADJPY CADCHF CHFJPY NZDJPY NZDCAD NZDCHF"

while true; do
    ready=0; missing=""
    for s in $FX; do
        n=$(ls /opt/arb/data/$s/2026-10-2*/*.csv 2>/dev/null | wc -l)
        if [ $n -ge 60 ]; then ready=$((ready+1)); else missing="$missing $s($n)"; fi
    done
    if [ $ready -eq 28 ]; then break; fi
    echo "[$(date -u +%H:%M)] $ready/28 paires, manque:$missing" >> $LOG
    sleep 900   # check toutes les 15 min
done

echo "[$(date -u +%H:%M)] 28/28 paires -> validation" >> $LOG
python3 /opt/arb/poc/data_structure.py >> $LOG 2>&1

echo "[$(date -u +%H:%M)] validation -> rescan graphe complet" >> $LOG
python3 poc_optuna.py scan >> $LOG 2>&1
echo "[$(date -u +%H:%M)] rescan termine: $(ls -la cand.npz | awk '{print $5}') octets" >> $LOG
echo "PIPELINE_FX_DONE" >> $LOG
