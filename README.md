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
required. Linux distributions without APT or DNF need a separate package setup. It does not install or
replace NVIDIA drivers or the CUDA toolkit; when `nvcc` and an NVIDIA GPU are already available it
builds and checks the CUDA generator, otherwise it builds only the CPU reference CLI.

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
