#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
elif command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
else
  echo "Python 3.11 or 3.12 is required. Please install Python 3.11 first." >&2
  exit 1
fi
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
printf '\nEnvironment ready. To run: .venv/bin/python -m uvicorn agenttest.main:app --reload\n'
