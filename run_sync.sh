#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python}"
ENTRYPOINT="$SCRIPT_DIR/sync_smartsheet_to_mysql.py"

usage() {
  cat <<'EOF'
Usage:
  ./run_sync.sh [options]

Examples:
  ./run_sync.sh --dry-run
  ./run_sync.sh --mysql-table CTM
  ./run_sync.sh --mysql-table CTM --mark-missing-as-deleted

Environment:
  PYTHON_BIN=/path/to/python  Override Python binary.

Notes:
  - The script expects a .env file in the project root.
  - All arguments are forwarded to sync_smartsheet_to_mysql.py.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$ENTRYPOINT" ]]; then
  echo "ERROR: Entry point not found: $ENTRYPOINT" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Python binary not found or not executable: $PYTHON_BIN" >&2
  echo "Create the virtualenv and install dependencies first:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "WARNING: .env was not found in $SCRIPT_DIR" >&2
  echo "Copy .env.example to .env and fill the real values if needed." >&2
fi

exec "$PYTHON_BIN" "$ENTRYPOINT" "$@"
