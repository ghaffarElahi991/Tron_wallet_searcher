#!/usr/bin/env bash
# Install/start PostgreSQL, then provision and migrate the TronForge database.
set -Eeuo pipefail
umask 077

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${TEST_DIR}/.." && pwd -P)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
MAIN_INSTALLER="${PROJECT_ROOT}/install.sh"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
BOOTSTRAP_DIR=""

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'Database installer error: %s\n' "$1" >&2; exit 1; }
cleanup() {
  if [[ "$BOOTSTRAP_DIR" == /tmp/tronforge-db-bootstrap.* && -d "$BOOTSTRAP_DIR" ]]; then
    rm -r -- "$BOOTSTRAP_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
run_as_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

[[ $# -eq 0 ]] || fail "Usage: ./test/db-install.sh"
[[ "$(uname -s)" == "Linux" ]] || fail "This script requires Linux."
[[ -x "$MAIN_INSTALLER" ]] || fail "install.sh is missing or is not executable."
[[ -f "${BACKEND_DIR}/.env" ]] || \
  fail "backend/.env is missing; run the main installer once before this repair script."
[[ -f "${BACKEND_DIR}/pyproject.toml" ]] || fail "backend/pyproject.toml is missing."

if [[ "$EUID" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "Run as root or install sudo."
  sudo -v
fi

PACKAGE_MANAGER=""
if command -v apt-get >/dev/null 2>&1; then
  PACKAGE_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
  PACKAGE_MANAGER="dnf"
else
  fail "Supported package managers are apt and dnf."
fi

step "Install PostgreSQL server and client packages"
if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
  run_as_root apt-get update
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl libpq-dev postgresql postgresql-client \
    python3 python3-pip python3-venv util-linux
else
  run_as_root dnf install -y \
    ca-certificates curl postgresql postgresql-devel postgresql-server \
    python3 python3-pip util-linux
fi

command -v psql >/dev/null 2>&1 || fail "psql is unavailable after package installation."
command -v pg_isready >/dev/null 2>&1 || \
  fail "pg_isready is unavailable after package installation."
if [[ "$EUID" -eq 0 ]]; then
  command -v runuser >/dev/null 2>&1 || fail "runuser is required when running as root."
fi

if [[ "$PACKAGE_MANAGER" == "dnf" && ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
  [[ ! -d /var/lib/pgsql/data || -z "$(ls -A /var/lib/pgsql/data)" ]] || \
    fail "PostgreSQL data directory is nonempty but is not initialized."
  command -v postgresql-setup >/dev/null 2>&1 || \
    fail "postgresql-setup is missing."
  run_as_root postgresql-setup --initdb
fi

step "Start PostgreSQL"
if command -v systemctl >/dev/null 2>&1; then
  run_as_root systemctl enable --now postgresql || run_as_root service postgresql start
elif command -v service >/dev/null 2>&1; then
  run_as_root service postgresql start
else
  fail "No supported service manager was found."
fi
pg_isready -q -h localhost -p 5432 || \
  fail "PostgreSQL is not accepting TCP connections on localhost:5432."

step "Prepare the Python 3.12 backend environment"
if [[ -d "${BACKEND_DIR}/.venv" && ! -x "$VENV_PYTHON" ]]; then
  fail "backend/.venv exists but is incomplete; move that directory aside and rerun this script."
fi
if [[ ! -x "$VENV_PYTHON" ]]; then
  command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable after package installation."
  BOOTSTRAP_DIR="$(mktemp -d /tmp/tronforge-db-bootstrap.XXXXXX)"
  python3 -m venv "${BOOTSTRAP_DIR}/venv" || \
    fail "System Python could not create a temporary virtual environment."
  "${BOOTSTRAP_DIR}/venv/bin/pip" install --disable-pip-version-check 'uv>=0.9,<1'
  "${BOOTSTRAP_DIR}/venv/bin/uv" venv --seed --python 3.12 "${BACKEND_DIR}/.venv"
fi
"$VENV_PYTHON" -c \
  'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"' || \
  fail "backend/.venv does not use Python 3.12; move it aside and rerun this script."

step "Install backend database, migration, and API dependencies"
if [[ -x "${BACKEND_DIR}/.venv/bin/pip" ]]; then
  (cd "$BACKEND_DIR" && "${BACKEND_DIR}/.venv/bin/pip" install -e '.[dev]')
else
  BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-$(mktemp -d /tmp/tronforge-db-bootstrap.XXXXXX)}"
  if [[ ! -x "${BOOTSTRAP_DIR}/venv/bin/uv" ]]; then
    python3 -m venv "${BOOTSTRAP_DIR}/venv"
    "${BOOTSTRAP_DIR}/venv/bin/pip" install --disable-pip-version-check 'uv>=0.9,<1'
  fi
  (cd "$BACKEND_DIR" && "${BOOTSTRAP_DIR}/venv/bin/uv" pip install \
    --python "$VENV_PYTHON" -e '.[dev]')
fi
[[ -x "${BACKEND_DIR}/.venv/bin/alembic" ]] || \
  fail "Alembic was not installed into backend/.venv."

step "Create or repair the TronForge role, database, permissions, and migrations"
unset TRONFORGE_DATABASE_URL 2>/dev/null || true
"$MAIN_INSTALLER" --database-only

step "Database installation complete"
printf 'Next, run: ./test/db-runner.sh\n'
