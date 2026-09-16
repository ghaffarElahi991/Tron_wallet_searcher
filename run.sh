#!/usr/bin/env bash
# Start the installed API, CUDA scheduler, Telegram bot, and watcher-free web UI.
set -Eeuo pipefail
umask 077

RUN_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKEND_DIR="${RUN_ROOT}/backend"
FRONTEND_DIR="${RUN_ROOT}/frontend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
UVICORN_BIN="${BACKEND_DIR}/.venv/bin/uvicorn"
NEXT_BIN="${FRONTEND_DIR}/node_modules/.bin/next"
LOG_DIR=""
PIDS=()

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'Launcher error: %s\n' "$1" >&2; exit 1; }

shutdown() {
  trap - EXIT INT TERM
  if [[ ${#PIDS[@]} -eq 0 ]]; then return; fi
  step "Stopping API, GPU scheduler, Telegram bot, and web UI"
  for child_pid in "${PIDS[@]}"; do
    kill -TERM "$child_pid" 2>/dev/null || true
  done
  for ((wait_second = 0; wait_second < 15; wait_second++)); do
    any_alive=0
    for child_pid in "${PIDS[@]}"; do
      if kill -0 "$child_pid" 2>/dev/null; then any_alive=1; fi
    done
    if [[ "$any_alive" -eq 0 ]]; then break; fi
    sleep 1
  done
  for child_pid in "${PIDS[@]}"; do
    if kill -0 "$child_pid" 2>/dev/null; then
      kill -KILL "$child_pid" 2>/dev/null || true
    fi
    wait "$child_pid" 2>/dev/null || true
  done
  if [[ -n "$LOG_DIR" ]]; then printf 'Logs retained in %s\n' "$LOG_DIR"; fi
}
trap shutdown EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $# -eq 0 || ( $# -eq 1 && "${1:-}" == "--check" ) ]] || fail "Usage: ./run.sh [--check]"
[[ -f "${BACKEND_DIR}/.env" ]] || fail "backend/.env is missing; run ./install.sh first."
[[ -x "$VENV_PYTHON" && -x "$UVICORN_BIN" ]] || \
  fail "Backend virtual environment is incomplete; run ./install.sh first."
[[ -x "$NEXT_BIN" ]] || fail "Frontend dependencies are missing; run ./install.sh first."
command -v npm >/dev/null 2>&1 || fail "npm is not on PATH; run ./install.sh first."
command -v curl >/dev/null 2>&1 || fail "curl is not on PATH; run ./install.sh first."
command -v nvidia-smi >/dev/null 2>&1 || \
  fail "nvidia-smi is missing; a visible NVIDIA GPU is required for real generation."

step "Check API, Telegram, and CUDA configuration"
(cd "$BACKEND_DIR" && "$VENV_PYTHON" - <<'PY'
import json
import os
import socket
import subprocess
from pathlib import Path

from app.config import get_settings

settings = get_settings()
if settings.generator_mode != "cuda":
    raise SystemExit("TRONFORGE_GENERATOR_MODE must be cuda to start the real GPU scheduler.")
if not settings.telegram_bot_token.get_secret_value().strip():
    raise SystemExit("TRONFORGE_TELEGRAM_BOT_TOKEN is empty in backend/.env.")

native_binary = Path(settings.generator_native_binary).resolve()
if not native_binary.is_file() or not os.access(native_binary, os.X_OK):
    raise SystemExit(f"CUDA generator is missing or not executable: {native_binary}")
try:
    result = subprocess.run(
        [str(native_binary), "gpu-info"], capture_output=True, text=True, check=True, timeout=15
    )
    gpu_info = json.loads(result.stdout)
except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
    raise SystemExit("CUDA generator could not query the GPUs; check its build and driver.") from exc
devices = gpu_info.get("devices", [])
if not gpu_info.get("cuda_compiled") or not devices:
    raise SystemExit("CUDA generator has no visible GPU; real wallet generation cannot start.")

for device in devices:
    device_index = device.get("index")
    if not isinstance(device_index, int) or device_index < 0:
        raise SystemExit("CUDA generator reported an invalid GPU device index.")
    try:
        result = subprocess.run(
            [str(native_binary), "gpu-self-test", "--device", str(device_index)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        self_test = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        details = getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)
        raise SystemExit(
            f"CUDA cryptographic self-test failed on GPU {device_index}: {details.strip()}"
        ) from exc
    if not self_test.get("passed") or not self_test.get("chained_four_limb_passed"):
        raise SystemExit(f"CUDA cryptographic self-test failed on GPU {device_index}.")

for port in (8000, 3000):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"Local port {port} is already in use; stop the existing service.") from exc

print(f"CUDA devices visible: {len(devices)}")
print("CUDA cryptographic self-tests passed on every visible GPU.")
print("API: 127.0.0.1:8000; web UI: 127.0.0.1:3000")
if settings.telegram_public_access:
    print("WARNING: Telegram public access is enabled; group users may see wallet private keys.")
PY
)

if [[ "${1:-}" == "--check" ]]; then
  printf 'Read-only launcher check passed.\n'
  exit 0
fi

step "Build the Next.js web UI once (no file watcher)"
(cd "$FRONTEND_DIR" && npm run build)

mkdir -p -- "${RUN_ROOT}/runtime-logs"
chmod 700 -- "${RUN_ROOT}/runtime-logs"
LOG_DIR="$(mktemp -d "${RUN_ROOT}/runtime-logs/run.XXXXXX")"

step "Start FastAPI"
(cd "$BACKEND_DIR" && exec env PYTHONUNBUFFERED=1 "$UVICORN_BIN" app.main:app \
  --host 127.0.0.1 --port 8000) >"${LOG_DIR}/api.log" 2>&1 &
API_PID=$!
PIDS+=("$API_PID")

api_ready=0
for ((attempt = 0; attempt < 60; attempt++)); do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    fail "API exited before becoming ready; inspect ${LOG_DIR}/api.log."
  fi
  sleep 1
done
[[ "$api_ready" -eq 1 ]] || fail "API/database did not become ready in 60 seconds; inspect ${LOG_DIR}/api.log."

step "Start the CUDA generation scheduler"
(cd "$BACKEND_DIR" && exec env PYTHONUNBUFFERED=1 "$VENV_PYTHON" -m app.generator) \
  >"${LOG_DIR}/generator.log" 2>&1 &
GENERATOR_PID=$!
PIDS+=("$GENERATOR_PID")

step "Start the Telegram bot"
(cd "$BACKEND_DIR" && exec env PYTHONUNBUFFERED=1 "$VENV_PYTHON" -m app.telegram_bot) \
  >"${LOG_DIR}/telegram.log" 2>&1 &
TELEGRAM_PID=$!
PIDS+=("$TELEGRAM_PID")

step "Start the watcher-free Next.js server"
(cd "$FRONTEND_DIR" && exec "$NEXT_BIN" start --hostname 127.0.0.1 --port 3000) \
  >"${LOG_DIR}/web.log" 2>&1 &
WEB_PID=$!
PIDS+=("$WEB_PID")

web_ready=0
for ((attempt = 0; attempt < 30; attempt++)); do
  if curl -fsS --max-time 2 http://127.0.0.1:3000/ >/dev/null 2>&1; then
    web_ready=1
    break
  fi
  for child_pid in "${PIDS[@]}"; do
    if ! kill -0 "$child_pid" 2>/dev/null; then
      fail "A service exited during startup; inspect logs in ${LOG_DIR}."
    fi
  done
  sleep 1
done
[[ "$web_ready" -eq 1 ]] || fail "Web UI did not become ready in 30 seconds; inspect ${LOG_DIR}/web.log."

printf '\nStack is running. UI: http://localhost:3000  API: http://localhost:8000\n'
printf 'Logs: %s\n' "$LOG_DIR"
printf 'Press Ctrl+C to stop all four services.\n'

if wait -n "${PIDS[@]}"; then
  fail "One service stopped unexpectedly; inspect logs in ${LOG_DIR}."
else
  child_status=$?
  printf 'A service exited with status %s; inspect logs in %s.\n' "$child_status" "$LOG_DIR" >&2
  exit "$child_status"
fi
