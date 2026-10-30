#!/bin/bash
# Installe les services: campagne (1 processus), monitoring (60 s), controle qualite (30 min).
# Usage (root): bash systemd_install.sh   puis   systemctl start arb-pull
set -euo pipefail

cat > /etc/systemd/system/arb-pull.service <<'EOF'
[Unit]
Description=Campagne ticks Dukascopy (file unique, 1 req/s)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/opt/arb/venv/bin/python /opt/arb/src/pull_all.py
WorkingDirectory=/opt/arb/src
Restart=on-failure
RestartSec=300
Nice=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/arb-monitor.service <<'EOF'
[Unit]
Description=Monitoring campagne (CPU/RAM/disque/progression)

[Service]
ExecStart=/bin/bash /opt/arb/src/monitor.sh
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/arb-verify.service <<'EOF'
[Unit]
Description=Controle qualite des mois termines

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'echo "=== $(date "+%%F %%T") ===" >> /opt/arb/logs/verify.log; /opt/arb/venv/bin/python /opt/arb/src/verify_coverage.py --requeue >> /opt/arb/logs/verify.log 2>&1'
EOF

cat > /etc/systemd/system/arb-verify.timer <<'EOF'
[Unit]
Description=Controle qualite toutes les 30 min

[Timer]
OnBootSec=10min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable arb-pull arb-monitor arb-verify.timer
systemctl start arb-monitor arb-verify.timer
echo "services installes (arb-pull active au boot, a demarrer: systemctl start arb-pull)"
