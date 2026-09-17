# TronForge web interface

Next.js client for the authenticated TronForge generation API.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

The FastAPI service must be running at `http://127.0.0.1:8000`. Browser requests use the
same-origin `/api/v1` URL and Next.js proxies them to FastAPI, so the UI also works when opened
through a server IP, domain, SSH tunnel, or HTTPS reverse proxy. To change the internal API URL,
copy the example environment file before building:

```bash
cp .env.local.example .env.local
```

Set `TRONFORGE_INTERNAL_API_URL` to the address reachable by the Next.js server. Do not set
`NEXT_PUBLIC_API_URL` to `localhost` for a remotely opened UI.

The backend must be migrated to the latest Alembic revision before web login. The UI
keeps its short-lived access token in memory and uses a rotating HttpOnly cookie to renew it in the
background, including after a page reload. Log in once again after upgrading from the old
session-storage login.

`npm run dev` performs a one-time production build and then starts the app
without a source-file watcher. This avoids Turbopack/inotify exhaustion on
machines with a busy file-watcher pool.

When hot reload is intentionally needed, use the lower-overhead Webpack watcher:

```bash
npm run dev:watch
```

## Connected features

- Single-operator API login, automatic session renewal, restoration, and logout
- Responsive wallet-pattern builder restricted to 3×4, 2×5, and 4×3
- Fixed, non-editable TRON `T` prefix with explicit Base58 character feedback
- First user-entered prefix character restricted to an uppercase Base58 letter or `9`
- Case-insensitive safe matching applied by default
- Dynamic difficulty and wait-time preview
- Real job creation, status polling, cancellation, and order history
- Live GPU-fleet status from the scheduler
- Server-generated wallet seed encrypted at rest
- Server-side result verification and final private-key assembly
- Local result verification with the address and private key displayed directly in the UI
- Funding amount validation, persisted request submission, and live status polling
- Confirmed transaction details and network-specific explorer links
- Security model and help content

## Safety boundary

Simulator GPU workers exercise scheduling but intentionally never return a wallet. CUDA mode is
required for real wallet results. Funding is independently disabled by default; its simulator mode
exercises the complete persisted funding workflow without moving tokens, while live mode is enabled
only through protected backend configuration.

The backend is the wallet custodian in this single-operator design. Protect its encryption key,
database, host, browser session, and backups accordingly. The UI deliberately displays the private
key on screen, so use it only in a private environment and never expose it through screenshots,
screen sharing, browser extensions, or logs. Production cryptography and custody boundaries must
receive an independent security audit before mainnet use.
