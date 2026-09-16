# TronForge wallet generator

## One-command local installation (APT and DNF Linux distributions)

Run the installer as root or as a normal user with `sudo` access from this directory:

```bash
./install.sh --check
./install.sh
```

It installs build tools and PostgreSQL on Debian/Ubuntu (APT) or Fedora/RHEL derivatives (DNF),
Node.js 24 when an existing Node.js is too old, Python 3.12 (using a managed Python when the
distribution does not ship it), backend dependencies, locked frontend dependencies, database
migrations, and native CPU/CUDA builds with self-tests. It also uses a temporary newer CMake
when the distribution's CMake is older than 3.24. OpenSSL 3 is required; the installer stops with a
clear error if the distribution supplies an older version. Root privileges and internet access are
required. Linux distributions without APT or DNF need a separate package setup. A visible NVIDIA
GPU is required for the default installation. If its driver works but `nvcc` is missing, the installer
adds NVIDIA's pinned CUDA 12.8 toolkit on supported x86_64 Ubuntu/Debian/RHEL-derived releases and
builds and self-tests the CUDA generator. It does not replace an existing NVIDIA driver. If the GPU
is present but the driver is missing on Ubuntu, run `./install.sh --install-driver`, reboot manually,
check `nvidia-smi -L`, and rerun `./install.sh`. On other distributions, install the driver using that
distribution's instructions first. Driver installation can affect kernel modules and may require
Secure Boot enrollment; the installer never reboots automatically. A machine without NVIDIA GPU
hardware cannot perform real GPU generation. For reference-only setup, run `./install.sh --cpu-only`.
That mode builds the CPU CLI but cannot run the CUDA scheduler in `./run.sh`.

For CUDA toolkits that support it, the installer enables bounded split compilation for the CUDA
source and `ptxas`. By default, both the CMake build-job limit and CUDA split-thread limit are
80% of the CPU cores available to the installer, rounded down (at least one). Set
`TRONFORGE_CUDA_COMPILE_THREADS=16 ./install.sh` to override only the split-thread limit; it is
capped at the available core count, and `1` disables split compilation. These are concurrency
limits, not a hard CPU-utilization cap: simultaneous C++ jobs can briefly exceed the 80% target,
and some compiler phases may use fewer cores. Split compilation can shorten some build phases but
does not change wallet-search speed by itself. The installer still runs the native and per-GPU
self-tests after building.

The installer keeps an existing `backend/.env` and database credentials
untouched. When the configured URL names the local `tronforge` PostgreSQL role/database, it creates
those targets only if they are missing. On a fresh machine without `backend/.env`, it creates that
file with random secrets. It never starts the API, generator,
frontend, or Telegram bot automatically; see [backend setup](backend/README.md) and
[frontend setup](frontend/README.md) for run commands. Review the generated admin password in
`backend/.env` before signing in, and keep that file private.
Files created by a root-run installation will be owned by root.

## Start the complete local stack

After installation, set `TRONFORGE_GENERATOR_MODE=cuda` and a valid
`TRONFORGE_TELEGRAM_BOT_TOKEN` in `backend/.env`, and make sure the native CUDA generator and
NVIDIA driver can see at least one GPU. Then run:

```bash
./run.sh --check
./run.sh
```

This builds the web UI once and starts FastAPI, the CUDA scheduler, Telegram, and the watcher-free
Next.js server. Both web services bind to `127.0.0.1`; from another computer, use an SSH tunnel for
ports 3000 and 8000 or configure a proper HTTPS reverse proxy. For an SSH tunnel, run this on your
computer and then open `http://localhost:3000`:

```bash
ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 root@SERVER_IP
```

Logs are kept under `runtime-logs/`.
Ctrl+C stops the four processes. The launcher runs in the foreground and does not survive an SSH
logout; use a service manager for unattended operation. It assumes PostgreSQL migrations were
applied by `./install.sh`.
