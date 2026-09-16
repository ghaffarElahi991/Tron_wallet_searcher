#!/usr/bin/env bash
# Linux installer for the local TronForge stack. Root or a sudo-capable user may run it.
set -Eeuo pipefail
umask 077

INSTALL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="${INSTALL_ROOT}/backend"
FRONTEND_DIR="${INSTALL_ROOT}/frontend"
NATIVE_DIR="${INSTALL_ROOT}/native"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
NODE_TEMP_DIR=""
PY_BOOTSTRAP_DIR=""
UV_BIN=""
CUDA_TEMP_DIR=""
CUDA_COMPILER=""
CPU_ONLY=0
INSTALL_DRIVER=0

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
  if [[ "$PY_BOOTSTRAP_DIR" == /tmp/tronforge-uv.* && -d "$PY_BOOTSTRAP_DIR" ]]; then
    rm -r -- "$PY_BOOTSTRAP_DIR" 2>/dev/null || true
  fi
  if [[ "$CUDA_TEMP_DIR" == /tmp/tronforge-cuda.* && -d "$CUDA_TEMP_DIR" ]]; then
    rm -r -- "$CUDA_TEMP_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'printf "Installer stopped at line %s. Existing .env and database were not reset.\n" "$LINENO" >&2' ERR

[[ -f "${BACKEND_DIR}/pyproject.toml" ]] || fail "backend/pyproject.toml is missing."
[[ -f "${BACKEND_DIR}/.env.example" ]] || fail "backend/.env.example is missing."
[[ -f "${FRONTEND_DIR}/package-lock.json" ]] || fail "frontend/package-lock.json is missing."
[[ -f "${NATIVE_DIR}/CMakeLists.txt" ]] || fail "native/CMakeLists.txt is missing."
[[ "$(uname -s)" == "Linux" ]] || fail "This installer requires Linux."
if [[ -r /etc/os-release ]]; then . /etc/os-release; fi

PACKAGE_MANAGER="unsupported"
case " ${ID:-} ${ID_LIKE:-} " in
  *ubuntu*|*debian*)
    if command -v apt-get >/dev/null 2>&1; then PACKAGE_MANAGER="apt"; fi
    ;;
  *fedora*|*rhel*|*centos*|*rocky*|*almalinux*|*amzn*)
    if command -v dnf >/dev/null 2>&1; then PACKAGE_MANAGER="dnf"; fi
    ;;
esac
if [[ "$PACKAGE_MANAGER" == "unsupported" ]]; then
  if command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1; then
    PACKAGE_MANAGER="apt"
  elif command -v dnf >/dev/null 2>&1 && command -v rpm >/dev/null 2>&1; then
    PACKAGE_MANAGER="dnf"
  fi
fi

nvidia_gpu_visible() {
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}
nvidia_gpu_hardware_present() {
  local vendor_file vendor pci_class
  for vendor_file in /sys/bus/pci/devices/*/vendor; do
    [[ -r "$vendor_file" ]] || continue
    IFS= read -r vendor < "$vendor_file" || continue
    [[ "$vendor" == "0x10de" ]] || continue
    IFS= read -r pci_class < "${vendor_file%/vendor}/class" || continue
    [[ "$pci_class" == 0x03* ]] && return 0
  done
  return 1
}
find_cuda_compiler() {
  local candidate
  if command -v nvcc >/dev/null 2>&1; then
    CUDA_COMPILER="$(command -v nvcc)"
    return 0
  fi
  for candidate in /usr/local/cuda/bin/nvcc /usr/local/cuda-*/bin/nvcc; do
    if [[ -x "$candidate" ]]; then
      CUDA_COMPILER="$candidate"
      return 0
    fi
  done
  return 1
}

case "${1:-}" in
  "") [[ $# -eq 0 ]] || fail "Usage: ./install.sh [--check|--cpu-only|--install-driver]" ;;
  --check|--cpu-only|--install-driver)
    [[ $# -eq 1 ]] || fail "Usage: ./install.sh [--check|--cpu-only|--install-driver]"
    ;;
  *) fail "Usage: ./install.sh [--check|--cpu-only|--install-driver]" ;;
esac
[[ "${1:-}" == "--cpu-only" ]] && CPU_ONLY=1
[[ "${1:-}" == "--install-driver" ]] && INSTALL_DRIVER=1

if [[ "${1:-}" == "--check" && $# -eq 1 ]]; then
  step "Read-only prerequisite check"
  printf 'Operating system: %s\n' "${PRETTY_NAME:-${ID:-unknown}}"
  printf 'Automatic package manager: %s\n' "$PACKAGE_MANAGER"
  for tool in sudo runuser python3 python3.12 node npm cmake c++ psql pg_isready nvcc nvidia-smi; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf '%-14s %s\n' "$tool" "$(command -v "$tool")"
    else
      printf '%-14s missing\n' "$tool"
    fi
  done
  if command -v openssl >/dev/null 2>&1; then
    printf 'OpenSSL version: %s\n' "$(openssl version)"
  fi
  if command -v cmake >/dev/null 2>&1; then
    printf 'CMake version: %s\n' "$(cmake --version | head -n 1)"
  fi
  if [[ "$PACKAGE_MANAGER" == "unsupported" ]]; then
    printf 'Automatic installation is unavailable for this package manager.\n'
  fi
  if [[ -f "${BACKEND_DIR}/.env" ]]; then
    printf 'backend/.env: present (will be preserved)\n'
  else
    printf 'backend/.env: absent (installer will create it)\n'
  fi
  if nvidia_gpu_visible; then
    printf 'NVIDIA GPU: visible through the driver\n'
  elif nvidia_gpu_hardware_present; then
    printf 'NVIDIA GPU: detected on PCI bus, but driver is not working\n'
  else
    printf 'NVIDIA GPU: not detected on PCI bus or through the driver\n'
  fi
  if find_cuda_compiler; then
    printf 'CUDA compiler: %s\n' "$CUDA_COMPILER"
  else
    printf 'CUDA compiler: missing; installer can add CUDA 12.8 on supported distributions\n'
  fi
  exit 0
fi
[[ "$PACKAGE_MANAGER" != "unsupported" ]] || \
  fail "Automatic installation supports Debian/Ubuntu (apt) and Fedora/RHEL derivatives (dnf); detected ${PRETTY_NAME:-${ID:-unknown}}."
if [[ "$EUID" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "sudo is required when running as a normal user."
  sudo -v
fi

if [[ "$CPU_ONLY" -eq 0 ]] && ! nvidia_gpu_visible; then
  if ! nvidia_gpu_hardware_present; then
    fail "No NVIDIA GPU is visible. A CUDA driver/toolkit cannot create GPU hardware; use a GPU machine, or run ./install.sh --cpu-only for reference-only setup."
  fi
  if [[ "$INSTALL_DRIVER" -eq 0 ]]; then
    fail "NVIDIA GPU hardware exists, but the driver cannot see it. On supported Ubuntu, run ./install.sh --install-driver (then reboot); otherwise install/fix the driver and rerun."
  fi
  [[ "${ID:-}" == "ubuntu" && "$PACKAGE_MANAGER" == "apt" ]] || \
    fail "Automatic NVIDIA driver setup is offered only on Ubuntu. Install the distribution's NVIDIA driver manually, reboot, then rerun ./install.sh."
  step "Install the Ubuntu-recommended NVIDIA compute driver"
  run_as_root apt-get update
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ubuntu-drivers-common
  if dpkg-query -W -f='${Package} ${Status}\n' 'nvidia-driver-*' 2>/dev/null | \
      awk '$2 == "install" && $3 == "ok" && $4 == "installed" {found=1} END {exit !found}'; then
    fail "An NVIDIA driver package is already installed but the GPU is not visible. Diagnose kernel modules, Secure Boot or reboot before changing drivers."
  fi
  run_as_root env DEBIAN_FRONTEND=noninteractive ubuntu-drivers install --gpgpu
  selected_driver="$(dpkg-query -W -f='${Package} ${Status}\n' 'nvidia-driver-*' 2>/dev/null | \
    awk '$2 == "install" && $3 == "ok" && $4 == "installed" && !found {print $1; found=1}' || true)"
  if [[ "$selected_driver" =~ ^nvidia-driver-([0-9]+)-server ]]; then
    run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "nvidia-utils-${BASH_REMATCH[1]}-server"
  elif [[ "$selected_driver" =~ ^nvidia-driver-([0-9]+) ]]; then
    run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "nvidia-utils-${BASH_REMATCH[1]}"
  else
    fail "Driver installation did not expose an installed NVIDIA driver package; check ubuntu-drivers output."
  fi
  fail "NVIDIA driver packages installed. Reboot this machine, confirm nvidia-smi -L works, then rerun ./install.sh. The installer will not reboot automatically."
fi

CUDA_REPO_CODE=""
if [[ "$CPU_ONLY" -eq 0 ]] && ! find_cuda_compiler; then
  [[ "$(uname -m)" == "x86_64" ]] || \
    fail "Automatic CUDA 12.8 toolkit setup currently requires x86_64; install a toolkit for this architecture manually."
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:20.04) CUDA_REPO_CODE="ubuntu2004" ;;
    ubuntu:22.04) CUDA_REPO_CODE="ubuntu2204" ;;
    ubuntu:24.04) CUDA_REPO_CODE="ubuntu2404" ;;
    debian:12) CUDA_REPO_CODE="debian12" ;;
    rhel:8|rhel:8.*|centos:8|centos:8.*|rocky:8|rocky:8.*|almalinux:8|almalinux:8.*) CUDA_REPO_CODE="rhel8" ;;
    rhel:9|rhel:9.*|centos:9|centos:9.*|rocky:9|rocky:9.*|almalinux:9|almalinux:9.*) CUDA_REPO_CODE="rhel9" ;;
    amzn:2023) CUDA_REPO_CODE="amzn2023" ;;
    *) fail "CUDA toolkit is missing, and this OS (${PRETTY_NAME:-${ID:-unknown}}) has no validated CUDA 12.8 repository in the installer. Install a compatible CUDA toolkit manually, then rerun; use --cpu-only only for reference setup." ;;
  esac
  driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | awk 'NR == 1 {print $1}')"
  [[ "$driver_version" =~ ^([0-9]+)\.([0-9]+) ]] || \
    fail "Cannot read the NVIDIA driver version; install a matching CUDA toolkit manually."
  if (( BASH_REMATCH[1] < 570 || ( BASH_REMATCH[1] == 570 && BASH_REMATCH[2] < 211 ) )); then
    fail "NVIDIA driver ${driver_version} is too old for the installer's pinned CUDA 12.8 toolkit. Update the driver or install a compatible older toolkit manually."
  fi
fi

step "Install ${PACKAGE_MANAGER} packages and PostgreSQL"
if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
  run_as_root apt-get update
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential ca-certificates cmake curl gnupg libpq-dev libssl-dev \
    openssl pkg-config postgresql postgresql-client python3 python3-pip \
    python3-venv util-linux
else
  run_as_root dnf install -y \
    ca-certificates cmake curl gcc gcc-c++ gnupg2 make openssl openssl-devel \
    pkgconf-pkg-config postgresql postgresql-devel postgresql-server \
    python3 python3-pip util-linux
fi
if [[ "$EUID" -eq 0 ]]; then
  command -v runuser >/dev/null 2>&1 || fail "runuser is required to manage PostgreSQL as root."
fi
openssl_major="$(openssl version | awk '{split($2, parts, "."); print parts[1]}')"
[[ "$openssl_major" =~ ^[0-9]+$ && "$openssl_major" -ge 3 ]] || \
  fail "The native wallet generator requires OpenSSL 3; this distribution supplies an older version."
if [[ -n "$CUDA_REPO_CODE" ]]; then
  step "Install NVIDIA CUDA 12.8 toolkit (driver packages are not replaced)"
  CUDA_TEMP_DIR="$(mktemp -d /tmp/tronforge-cuda.XXXXXX)"
  if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
    curl -fsSL \
      "https://developer.download.nvidia.com/compute/cuda/repos/${CUDA_REPO_CODE}/x86_64/cuda-keyring_1.1-1_all.deb" \
      -o "${CUDA_TEMP_DIR}/cuda-keyring.deb"
    run_as_root dpkg -i "${CUDA_TEMP_DIR}/cuda-keyring.deb"
    run_as_root apt-get update
    run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y cuda-toolkit-12-8
  else
    cuda_repo_url="https://developer.download.nvidia.com/compute/cuda/repos/${CUDA_REPO_CODE}/x86_64/cuda-${CUDA_REPO_CODE}.repo"
    curl -fsSL "$cuda_repo_url" -o "${CUDA_TEMP_DIR}/cuda.repo"
    if [[ ! -e /etc/yum.repos.d/tronforge-cuda.repo ]]; then
      run_as_root install -m 0644 "${CUDA_TEMP_DIR}/cuda.repo" /etc/yum.repos.d/tronforge-cuda.repo
    elif ! run_as_root cmp -s "${CUDA_TEMP_DIR}/cuda.repo" /etc/yum.repos.d/tronforge-cuda.repo; then
      fail "Existing /etc/yum.repos.d/tronforge-cuda.repo differs from NVIDIA's repository; review it manually."
    fi
    run_as_root dnf install -y cuda-toolkit-12-8
  fi
  find_cuda_compiler || fail "CUDA toolkit package installed but nvcc was not found under /usr/local/cuda*/bin."
fi
if [[ "$PACKAGE_MANAGER" == "dnf" ]] && ! pg_isready -q; then
  if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
    [[ ! -d /var/lib/pgsql/data || -z "$(ls -A /var/lib/pgsql/data)" ]] || \
      fail "PostgreSQL data directory is nonempty but has no PG_VERSION; initialize it manually."
    command -v postgresql-setup >/dev/null 2>&1 || \
      fail "postgresql-setup is missing; initialize PostgreSQL manually."
    run_as_root postgresql-setup --initdb
  fi
fi
if command -v systemctl >/dev/null 2>&1; then
  run_as_root systemctl enable --now postgresql || run_as_root service postgresql start
else
  if command -v service >/dev/null 2>&1; then
    run_as_root service postgresql start
  else
    fail "No supported PostgreSQL service manager was found; start PostgreSQL manually."
  fi
fi
node_is_supported() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && \
    node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)'
}

if ! node_is_supported; then
  step "Install Node.js 24 from the signed NodeSource ${PACKAGE_MANAGER} repository"
  if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
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
  else
    node_arch="$(uname -m)"
    [[ "$node_arch" == "x86_64" || "$node_arch" == "aarch64" ]] || \
      fail "NodeSource RPMs require x86_64 or aarch64; install Node.js >=20.9 manually."
    printf '%s\n' \
      '[tronforge-nodejs]' \
      'name=NodeSource Node.js 24 for TronForge' \
      "baseurl=https://rpm.nodesource.com/pub_24.x/nodistro/nodejs/${node_arch}" \
      'enabled=1' \
      'gpgcheck=1' \
      'gpgkey=https://rpm.nodesource.com/gpgkey/ns-operations-public.key' \
      'module_hotfixes=1' \
      | run_as_root tee /etc/yum.repos.d/tronforge-nodejs.repo >/dev/null
    run_as_root dnf install -y nodejs --enablerepo=tronforge-nodejs
  fi
  hash -r
  node_is_supported || fail "Node.js >=20.9 is still not on PATH; switch your shell's Node version."
fi

ensure_tool_venv() {
  if [[ -n "$PY_BOOTSTRAP_DIR" ]]; then return; fi
  PY_BOOTSTRAP_DIR="$(mktemp -d /tmp/tronforge-uv.XXXXXX)"
  python3 -m venv "$PY_BOOTSTRAP_DIR/venv" || \
    fail "System Python cannot create a temporary venv; install its venv package."
}

ensure_uv() {
  if [[ -n "$UV_BIN" ]]; then return; fi
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi
  ensure_tool_venv
  "$PY_BOOTSTRAP_DIR/venv/bin/pip" install --disable-pip-version-check 'uv>=0.9,<1'
  UV_BIN="$PY_BOOTSTRAP_DIR/venv/bin/uv"
}

step "Prepare Python 3.12 virtual environment"
if [[ -d "${BACKEND_DIR}/.venv" && ! -x "$VENV_PYTHON" ]]; then
  fail "backend/.venv exists but has no working Python; move it aside manually before retrying."
fi
if [[ ! -x "$VENV_PYTHON" ]]; then
  ensure_uv
  "$UV_BIN" venv --seed --python 3.12 "${BACKEND_DIR}/.venv"
fi
"$VENV_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'

step "Install FastAPI, generator, Telegram, migration and test libraries"
if [[ -x "${BACKEND_DIR}/.venv/bin/pip" ]]; then
  (cd "$BACKEND_DIR" && "${BACKEND_DIR}/.venv/bin/pip" install -e '.[dev]')
else
  ensure_uv
  (cd "$BACKEND_DIR" && "$UV_BIN" pip install --python "$VENV_PYTHON" -e '.[dev]')
fi

CUDA_READY=0
if [[ "$CPU_ONLY" -eq 0 ]] && find_cuda_compiler && nvidia_gpu_visible; then
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
  "$VENV_PYTHON" - "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env" "$CUDA_READY" <<'PY'
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

cmake_is_supported() {
  command -v cmake >/dev/null 2>&1 || return 1
  local details
  details="$(cmake --version)"
  if [[ "$details" =~ cmake\ version\ ([0-9]+)\.([0-9]+) ]]; then
    [[ "${BASH_REMATCH[1]}" -gt 3 || ( "${BASH_REMATCH[1]}" -eq 3 && "${BASH_REMATCH[2]}" -ge 24 ) ]]
  else
    return 1
  fi
}

if ! cmake_is_supported; then
  step "Install CMake >=3.24 for this project without replacing the system CMake"
  ensure_tool_venv
  "$PY_BOOTSTRAP_DIR/venv/bin/pip" install --disable-pip-version-check 'cmake>=3.24,<4'
  export PATH="$PY_BOOTSTRAP_DIR/venv/bin:$PATH"
  cmake_is_supported || fail "CMake >=3.24 is still unavailable."
fi

available_cpu_cores="$(nproc)"
[[ "$available_cpu_cores" =~ ^[1-9][0-9]*$ ]] || \
  fail "Could not determine the number of available CPU cores."
build_parallel_jobs=$(( available_cpu_cores * 80 / 100 ))
if (( build_parallel_jobs < 1 )); then build_parallel_jobs=1; fi
printf 'CMake build jobs: up to %s for %s available CPU cores (about 80%%).\n' \
  "$build_parallel_jobs" "$available_cpu_cores"

step "Build and self-test CPU native reference"
cmake -S "$NATIVE_DIR" -B "${NATIVE_DIR}/build" \
  -DCMAKE_BUILD_TYPE=Release -DTRONFORGE_ENABLE_CUDA=OFF
cmake --build "${NATIVE_DIR}/build" --parallel "$build_parallel_jobs"
ctest --test-dir "${NATIVE_DIR}/build" --output-on-failure

if [[ "$CUDA_READY" -eq 1 ]]; then
  step "Build and self-test CUDA wallet generator"
  cuda_cmake_flags=()
  cuda_help="$("$CUDA_COMPILER" --help)"
  if [[ "$cuda_help" == *"--split-compile"* ]]; then
    cuda_compile_threads="${TRONFORGE_CUDA_COMPILE_THREADS:-$build_parallel_jobs}"
    [[ "$cuda_compile_threads" =~ ^[1-9][0-9]{0,4}$ ]] || \
      fail "TRONFORGE_CUDA_COMPILE_THREADS must be a positive integer (up to 99999)."
    if (( cuda_compile_threads > available_cpu_cores )); then
      cuda_compile_threads="$available_cpu_cores"
    fi
    if (( cuda_compile_threads > 1 )); then
      cuda_compile_flags="--split-compile=${cuda_compile_threads}"
      ptxas_path="${CUDA_COMPILER%/*}/ptxas"
      if [[ -x "$ptxas_path" ]]; then
        ptxas_help="$("$ptxas_path" --help 2>/dev/null || true)"
        if [[ "$ptxas_help" == *"--split-compile"* ]]; then
          cuda_compile_flags+=" -Xptxas=--split-compile=${cuda_compile_threads}"
        fi
      fi
      cuda_cmake_flags=("-DCMAKE_CUDA_FLAGS=${cuda_compile_flags}")
      printf 'CUDA compiler split compilation: up to %s of %s available CPU threads\n' \
        "$cuda_compile_threads" "$available_cpu_cores"
    fi
  else
    printf 'CUDA compiler does not support split compilation; using standard build flags.\n'
  fi
  cmake --fresh -S "$NATIVE_DIR" -B "${NATIVE_DIR}/build-cuda" \
    -DCMAKE_BUILD_TYPE=Release -DTRONFORGE_REQUIRE_CUDA=ON \
    -DCMAKE_CUDA_COMPILER="$CUDA_COMPILER" \
    -DCMAKE_CUDA_ARCHITECTURES=native "${cuda_cmake_flags[@]}"
  cmake --build "${NATIVE_DIR}/build-cuda" --parallel "$build_parallel_jobs"
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
  printf '\nCPU-only mode: reference CLI built; real GPU generation is unavailable.\n'
fi

step "Installation complete"
printf 'PostgreSQL and migrations, Python libraries, frontend dependencies and native build are ready.\n'
if [[ "$NEW_ENV" -eq 1 ]]; then
  printf 'New credentials are in backend/.env; keep that file private and review its settings.\n'
else
  printf 'Existing backend/.env was preserved.\n'
fi
printf 'This installer does not start the API, generator, web UI or Telegram bot.\n'
