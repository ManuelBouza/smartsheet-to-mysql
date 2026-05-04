#!/usr/bin/env bash
set -Eeuo pipefail

trap 'handle_error "$LINENO" "$BASH_COMMAND"' ERR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_NAME="$(basename -- "${BASH_SOURCE[0]}")"
PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python}"
ENTRYPOINT="$SCRIPT_DIR/sync_smartsheet_to_mysql.py"
ENV_FILE="$SCRIPT_DIR/.env"

log_info() {
  printf '[%s] INFO: %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >&2
}

log_warn() {
  printf '[%s] WARN: %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >&2
}

log_error() {
  printf '[%s] ERROR: %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >&2
}

handle_error() {
  local -r line_no="$1"
  local -r command="$2"

  log_error "Command failed on line ${line_no}: ${command}"
}

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

validate_runtime() {
  if [[ ! -f "$ENTRYPOINT" ]]; then
    log_error "Entry point not found: $ENTRYPOINT"
    return 1
  fi

  if [[ ! -x "$PYTHON_BIN" ]]; then
    log_error "Python binary not found or not executable: $PYTHON_BIN"
    log_error "Create the virtualenv and install dependencies first:"
    log_error "  python3 -m venv .venv"
    log_error "  .venv/bin/pip install -r requirements.txt"
    return 1
  fi

  if [[ ! -f "$ENV_FILE" ]]; then
    log_warn ".env was not found in $SCRIPT_DIR"
    log_warn "Copy .env.example to .env and fill the real values if needed."
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

validate_runtime

log_info "Launching ${SCRIPT_NAME} with Python: $PYTHON_BIN"
exec "$PYTHON_BIN" "$ENTRYPOINT" "$@"
