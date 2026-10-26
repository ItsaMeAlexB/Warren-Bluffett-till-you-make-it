#!/usr/bin/env bash
# install.sh - self-contained dependency install (skips what's already present).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
PY=python3
command -v "$PY" >/dev/null || PY=python

# prefer a project venv; fall back to --user (--break-system-packages on
# externally-managed / PEP-668 distros). No torch is ever installed.
if ! "$PY" -c "import yfinance, openpyxl, vaderSentiment" >/dev/null 2>&1; then
  echo "==> Installing dependencies (yfinance, pandas, openpyxl, requests, vaderSentiment)"
  if "$PY" -m venv .venv >/dev/null 2>&1; then
    ./.venv/bin/pip install --upgrade pip >/dev/null
    ./.venv/bin/pip install -r requirements.txt
    RUN="./.venv/bin/python main.py"
  else
    "$PY" -m pip install --user --break-system-packages -r requirements.txt \
      || "$PY" -m pip install --user -r requirements.txt
    RUN="$PY main.py"
  fi
else
  echo "==> Dependencies already present."
  [ -x .venv/bin/python ] && RUN=".venv/bin/python main.py" || RUN="$PY main.py"
fi

echo "==> Done. Run a scan with:   $RUN"

