# TronForge wallet generator

## One-command local installation (Ubuntu 24.04)

Run the installer as your normal user from this directory:

```bash
./install.sh --check
./install.sh
```

It installs Ubuntu build tools and PostgreSQL, Node.js 24 when an existing Node.js is too old,
the Python 3.12 backend dependencies, locked frontend dependencies, database migrations, and native
CPU/CUDA builds with self-tests. `sudo` and internet access are required. It does not install or
replace NVIDIA drivers or the CUDA toolkit; when `nvcc` and an NVIDIA GPU are already available it
builds and checks the CUDA generator, otherwise it builds only the CPU reference CLI.

The installer keeps an existing `backend/.env` and database credentials
untouched. When the configured URL names the local `tronforge` PostgreSQL role/database, it creates
those targets only if they are missing. On a fresh machine without `backend/.env`, it creates that
file with random secrets. It never starts the API, generator,
frontend, or Telegram bot automatically; see [backend setup](backend/README.md) and
[frontend setup](frontend/README.md) for run commands. Review the generated admin password in
`backend/.env` before signing in, and keep that file private.
