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
