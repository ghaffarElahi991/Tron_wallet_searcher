#!/usr/bin/env bash
# Ubuntu 24.04 installer for the local TronForge stack. Root or a sudo-capable user may run it.
set -Eeuo pipefail
umask 077

INSTALL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="${INSTALL_ROOT}/backend"
FRONTEND_DIR="${INSTALL_ROOT}/frontend"
NATIVE_DIR="${INSTALL_ROOT}/native"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
NODE_TEMP_DIR=""

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'Installer error: %s\n' "$1" >&2; exit 1; }
run_as_root() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}
run_as_postgres() {
  (
    cd /tmp
    if [[ "$EUID" -eq 0 ]]; then
      runuser -u postgres -- "$@"
    else
      sudo -u postgres -- "$@"
    fi
  )
}
cleanup() {
  if [[ -n "$NODE_TEMP_DIR" && -d "$NODE_TEMP_DIR" ]]; then
    rm -f -- "$NODE_TEMP_DIR/nodesource.key" "$NODE_TEMP_DIR/nodesource.gpg"
    rmdir -- "$NODE_TEMP_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'printf "Installer stopped at line %s. Existing .env and database were not reset.\n" "$LINENO" >&2' ERR

[[ -f "${BACKEND_DIR}/pyproject.toml" ]] || fail "backend/pyproject.toml is missing."
[[ -f "${BACKEND_DIR}/.env.example" ]] || fail "backend/.env.example is missing."
[[ -f "${FRONTEND_DIR}/package-lock.json" ]] || fail "frontend/package-lock.json is missing."
[[ -f "${NATIVE_DIR}/CMakeLists.txt" ]] || fail "native/CMakeLists.txt is missing."

if [[ "${1:-}" == "--check" && $# -eq 1 ]]; then
  step "Read-only prerequisite check"
  . /etc/os-release
  printf 'Operating system: %s %s\n' "$ID" "$VERSION_ID"
  for tool in sudo runuser python3.12 node npm cmake c++ psql pg_isready nvcc nvidia-smi; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf '%-14s %s\n' "$tool" "$(command -v "$tool")"
    else
      printf '%-14s missing\n' "$tool"
    fi
  done
  if [[ -f "${BACKEND_DIR}/.env" ]]; then
    printf 'backend/.env: present (will be preserved)\n'
  else
    printf 'backend/.env: absent (installer will create it)\n'
  fi
  if command -v nvcc >/dev/null 2>&1 && command -v nvidia-smi >/dev/null 2>&1 \
    && nvidia-smi -L >/dev/null 2>&1; then
    printf 'CUDA GPU: visible (installer will build CUDA)\n'
  else
    printf 'CUDA GPU: not visible (installer will build CPU reference only)\n'
  fi
  exit 0
fi
[[ $# -eq 0 ]] || fail "Usage: ./install.sh [--check]"

. /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" ]] || \
  fail "This installer currently supports Ubuntu 24.04."
if [[ "$EUID" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "sudo is required when running as a normal user."
  sudo -v
fi

step "Install Ubuntu packages and PostgreSQL"
run_as_root apt-get update
run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential ca-certificates cmake curl gnupg libpq-dev libssl-dev \
  openssl pkg-config postgresql postgresql-client python3.12 python3.12-venv util-linux
if [[ "$EUID" -eq 0 ]]; then
  command -v runuser >/dev/null 2>&1 || fail "runuser is required to manage PostgreSQL as root."
fi
if command -v systemctl >/dev/null 2>&1; then
  run_as_root systemctl enable --now postgresql || run_as_root service postgresql start
else
  run_as_root service postgresql start
fi

node_is_supported() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && \
    node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)'
}

if ! node_is_supported; then
  step "Install Node.js 24 from the signed NodeSource APT repository"
  NODE_TEMP_DIR="$(mktemp -d /tmp/tronforge-node.XXXXXX)"
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    -o "$NODE_TEMP_DIR/nodesource.key"
  gpg --batch --yes --dearmor -o "$NODE_TEMP_DIR/nodesource.gpg" \
    "$NODE_TEMP_DIR/nodesource.key"
  run_as_root install -m 0644 "$NODE_TEMP_DIR/nodesource.gpg" /usr/share/keyrings/tronforge-nodesource.gpg
  printf '%s\n' \
    'deb [signed-by=/usr/share/keyrings/tronforge-nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main' \
    | run_as_root tee /etc/apt/sources.list.d/tronforge-nodesource.list >/dev/null
  run_as_root apt-get update
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  hash -r
  node_is_supported || fail "Node.js >=20.9 is still not on PATH; switch your shell's Node version."
fi

step "Prepare Python 3.12 virtual environment"
if [[ -d "${BACKEND_DIR}/.venv" && ! -x "$VENV_PYTHON" ]]; then
  fail "backend/.venv exists but has no working Python; move it aside manually before retrying."
fi
if [[ ! -x "$VENV_PYTHON" ]]; then
  python3.12 -m venv "${BACKEND_DIR}/.venv"
fi
"$VENV_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'

step "Install FastAPI, generator, Telegram, migration and test libraries"
(cd "$BACKEND_DIR" && "${BACKEND_DIR}/.venv/bin/pip" install -e '.[dev]')

CUDA_READY=0
if command -v nvcc >/dev/null 2>&1 && command -v nvidia-smi >/dev/null 2>&1 \
  && nvidia-smi -L >/dev/null 2>&1; then
  CUDA_READY=1
fi

NEW_ENV=0
if [[ ! -e "${BACKEND_DIR}/.env" ]]; then
  step "Check that fresh local PostgreSQL targets are unused"
  postgres_port="$(run_as_postgres psql -X -At -d postgres -c 'SHOW port')"
  [[ "$postgres_port" == "5432" ]] || \
    fail "Local PostgreSQL is not using port 5432; configure backend/.env manually."
  role_exists="$(run_as_postgres psql -X -At -d postgres -c \
    "SELECT 1 FROM pg_roles WHERE rolname = 'tronforge'")"
  db_exists="$(run_as_postgres psql -X -At -d postgres -c \
    "SELECT 1 FROM pg_database WHERE datname = 'tronforge'")"
  if [[ "$role_exists" == "1" || "$db_exists" == "1" ]]; then
    fail "A tronforge role/database already exists but backend/.env does not; configure existing credentials manually."
  fi
  step "Create new secure backend/.env (existing files are never overwritten)"
  NEW_ENV=1
  python3.12 - "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env" "$CUDA_READY" <<'PY'
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

example = Path(sys.argv[1])
destination = Path(sys.argv[2])
cuda_ready = sys.argv[3] == "1"
db_password = secrets.token_urlsafe(32)
replacements = {
    "TRONFORGE_DATABASE_URL": (
        "postgresql+psycopg://tronforge:"
        f"{quote(db_password, safe='')}@localhost:5432/tronforge"
    ),
    "TRONFORGE_JWT_SECRET": secrets.token_urlsafe(48),
    "TRONFORGE_WORKER_API_KEY": secrets.token_urlsafe(48),
    "TRONFORGE_RESULT_ENCRYPTION_KEY": secrets.token_urlsafe(48),
    "TRONFORGE_ADMIN_PASSWORD": "TronForge-" + secrets.token_urlsafe(24),
    "TRONFORGE_GENERATOR_MODE": "cuda" if cuda_ready else "simulator",
}
lines = []
for line in example.read_text(encoding="utf-8").splitlines():
    key, separator, _value = line.partition("=")
    lines.append(f"{key}={replacements[key]}" if separator and key in replacements else line)
with destination.open("x", encoding="utf-8") as env_file:
    env_file.write("\n".join(lines) + "\n")
os.chmod(destination, 0o600)
print("Created backend/.env with new random credentials (values not displayed).")
PY
fi

step "Provision or verify PostgreSQL connection"
(cd "$BACKEND_DIR" && "$VENV_PYTHON" - "$NEW_ENV" "$EUID" <<'PY'
import subprocess
import sys

import psycopg
from sqlalchemy.engine import make_url

from app.config import get_settings

fresh_env = sys.argv[1] == "1"
postgres_runner = ["runuser", "-u", "postgres", "--"] if sys.argv[2] == "0" else [
    "sudo", "-u", "postgres", "--"
]
url = make_url(get_settings().database_url)
if url.drivername != "postgresql+psycopg":
    raise SystemExit("Installer requires a postgresql+psycopg database URL in backend/.env.")
if not all((url.host, url.database, url.username, url.password)):
    raise SystemExit("Database URL in backend/.env is incomplete.")

dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)

local_target = (url.host, url.port or 5432, url.username, url.database) == (
    "localhost", 5432, "tronforge", "tronforge"
)
if fresh_env and not local_target:
    raise SystemExit("New installation can auto-provision only the local tronforge database.")

if local_target:
    def postgres_query(statement: str) -> str:
        result = subprocess.run(
            [*postgres_runner, "psql", "-X", "-At", "-d", "postgres", "-c", statement],
            capture_output=True,
            text=True,
            check=True,
            cwd="/tmp",
        )
        return result.stdout.strip()

    if postgres_query("SHOW port") != "5432":
        raise SystemExit("Local PostgreSQL is not using port 5432; no role or database was changed.")
    role_exists = postgres_query("SELECT 1 FROM pg_roles WHERE rolname = 'tronforge'") == "1"
    db_exists = postgres_query("SELECT 1 FROM pg_database WHERE datname = 'tronforge'") == "1"
    if db_exists and not role_exists:
        raise SystemExit("A tronforge database already exists without its role; no changes made.")
    if role_exists:
        try:
            check_db = "tronforge" if db_exists else "postgres"
            check_dsn = url.set(drivername="postgresql", database=check_db).render_as_string(
                hide_password=False
            )
            with psycopg.connect(check_dsn, connect_timeout=5):
                pass
        except psycopg.Error as exc:
            raise SystemExit(
                "Existing tronforge role has a different password; edit backend/.env instead of resetting it."
            ) from exc
    else:
        password_literal = "'" + url.password.replace("'", "''") + "'"
        subprocess.run(
            [*postgres_runner, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "postgres"],
            input=f"CREATE ROLE tronforge LOGIN PASSWORD {password_literal};\n",
            capture_output=True,
            text=True,
            check=True,
            cwd="/tmp",
        )
    if not db_exists:
        subprocess.run(
            [*postgres_runner, "createdb", "-O", "tronforge", "tronforge"],
            capture_output=True,
            text=True,
            check=True,
            cwd="/tmp",
        )

try:
    with psycopg.connect(dsn, connect_timeout=5) as db:
        db.execute("SELECT 1")
except psycopg.Error as exc:
    raise SystemExit("Cannot connect using backend/.env; database and credentials were preserved.") from exc
print("PostgreSQL connection verified.")
PY
)

step "Apply PostgreSQL migrations"
(cd "$BACKEND_DIR" && "${BACKEND_DIR}/.venv/bin/alembic" upgrade head)

step "Install locked Next.js dependencies"
(cd "$FRONTEND_DIR" && npm ci --no-audit --no-fund)

step "Build and self-test CPU native reference"
cmake -S "$NATIVE_DIR" -B "${NATIVE_DIR}/build" \
  -DCMAKE_BUILD_TYPE=Release -DTRONFORGE_ENABLE_CUDA=OFF
cmake --build "${NATIVE_DIR}/build" --parallel
ctest --test-dir "${NATIVE_DIR}/build" --output-on-failure

if [[ "$CUDA_READY" -eq 1 ]]; then
  step "Build and self-test CUDA wallet generator"
  cmake -S "$NATIVE_DIR" -B "${NATIVE_DIR}/build-cuda" \
    -DCMAKE_BUILD_TYPE=Release -DTRONFORGE_REQUIRE_CUDA=ON \
    -DCMAKE_CUDA_COMPILER="$(command -v nvcc)" \
    -DCMAKE_CUDA_ARCHITECTURES=native
  cmake --build "${NATIVE_DIR}/build-cuda" --parallel
  ctest --test-dir "${NATIVE_DIR}/build-cuda" --output-on-failure
  gpu_info="$("${NATIVE_DIR}/build-cuda/tronforge-generator" gpu-info)"
  gpu_indices="$(printf '%s\n' "$gpu_info" | "$VENV_PYTHON" -c '
import json, sys
info = json.load(sys.stdin)
devices = info.get("devices", [])
if not info.get("cuda_compiled") or not devices:
    raise SystemExit("CUDA build did not discover a GPU")
for device in devices:
    print(device["index"])
')"
  while IFS= read -r gpu_index; do
    [[ "$gpu_index" =~ ^[0-9]+$ ]] || fail "Native gpu-info reported an invalid device index."
    "${NATIVE_DIR}/build-cuda/tronforge-generator" gpu-self-test --device "$gpu_index"
  done <<< "$gpu_indices"
else
  printf '\nCUDA driver/toolkit or GPU not detected: CPU reference built; real GPU generation is unavailable.\n'
fi

step "Installation complete"
printf 'PostgreSQL and migrations, Python libraries, frontend dependencies and native build are ready.\n'
if [[ "$NEW_ENV" -eq 1 ]]; then
  printf 'New credentials are in backend/.env; keep that file private and review its settings.\n'
else
  printf 'Existing backend/.env was preserved.\n'
fi
printf 'This installer does not start the API, generator, web UI or Telegram bot.\n'
