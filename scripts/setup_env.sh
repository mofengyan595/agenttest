#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
else
  echo "Python 3.11 is required. Please install it first." >&2
  exit 1
fi
if [ ! -f .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 11)'
LOCK=requirements-dev.lock
EXTRAS='.[dev]'
if [ "${1:-}" = "--agent" ]; then
  LOCK=requirements-agent.lock
  EXTRAS='.[dev,agent,mcp]'
elif [ "$#" -ne 0 ]; then
  echo 'Usage: setup_env.sh [--agent]' >&2
  exit 1
fi
.venv/bin/python -m pip install -r "$LOCK"
.venv/bin/python -m pip install --no-build-isolation --no-deps -e "$EXTRAS"
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
printf '\nEnvironment ready. To run: .venv/bin/python -m uvicorn agenttest.main:app --reload\n'
