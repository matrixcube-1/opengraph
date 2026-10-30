#!/bin/bash
# Installation complete d'une instance neuve (Ubuntu 24.04 LTS) pour la campagne ticks.
# Usage (root): bash bootstrap.sh     -- idempotent, peut etre relance
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "== 1. paquets systeme"
timedatectl set-timezone UTC || true
apt-get update -q
apt-get install -yq curl ca-certificates gnupg xvfb jq python3-venv python3-pip

echo "== 2. Node.js 24 (NodeSource)"
if ! node --version 2>/dev/null | grep -q '^v24'; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -yq nodejs
fi

echo "== 3. dukascopy-node 1.51.0 + navigateur"
npm install -g dukascopy-node@1.51.0
DN=$(npm root -g)/dukascopy-node
(cd "$DN" && npx --yes playwright-core install --with-deps chromium)
if ! command -v google-chrome >/dev/null; then
  curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb
  apt-get install -yq /tmp/chrome.deb
fi

echo "== 4. environnement Python (versions identiques a l'ancien serveur)"
mkdir -p /opt/arb/src /opt/arb/data /opt/arb/logs
[ -d /opt/arb/venv ] || python3 -m venv /opt/arb/venv
/opt/arb/venv/bin/pip install -q --upgrade pip
/opt/arb/venv/bin/pip install -q \
  optuna==5.0.0 optuna-dashboard==0.21.0 numpy==2.5.4 polars==2.1.0 pyarrow==26.0.0 \
  duckdb==2.0.0 scipy==1.18.1 numba==0.68.0 matplotlib==3.11.2

echo "== 5. versions installees"
lsb_release -ds; node --version; npm --version
npm ls -g --depth=0 | grep dukascopy
google-chrome --version
/opt/arb/venv/bin/python -c "import optuna, polars, duckdb, numpy; print('optuna', optuna.__version__, '| polars', polars.__version__, '| duckdb', duckdb.__version__, '| numpy', numpy.__version__)"
echo "== BOOTSTRAP OK"
