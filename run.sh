#!/usr/bin/env bash
#
# Activate the venv, sync with DEGIRO, then launch the dashboard.
#
# Usage:
#   ./run.sh              # full sync (logs in to DEGIRO — needs phone approval), then dashboard
#   ./run.sh --offline    # re-run derivations from stored data (no login), then dashboard
#   ./run.sh --no-sync    # skip the sync entirely, just open the dashboard
#
set -euo pipefail

# Always run from the project root (the dir this script lives in).
cd "$(dirname "$0")"

VENV_ACTIVATE=".venv/Scripts/activate"
if [[ ! -f "$VENV_ACTIVATE" ]]; then
    echo "error: virtualenv not found at $VENV_ACTIVATE" >&2
    echo "create it first, e.g.:  python -m venv .venv && .venv/Scripts/pip install -r requirements.txt" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

# Pass through any sync flags (e.g. --offline). --no-sync skips syncing.
if [[ "${1:-}" == "--no-sync" ]]; then
    echo ">> skipping sync (--no-sync)"
else
    echo ">> syncing with DEGIRO..."
    python scripts/sync.py "$@"
fi

echo ">> launching dashboard..."
exec streamlit run dashboard/app.py
