# TronForge web interface

Next.js client for the authenticated TronForge generation API.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

The FastAPI service must be running at `http://localhost:8000`. To use another URL, copy the
example environment file and change it before building:

```bash
cp .env.local.example .env.local
```

The backend must be migrated to the `0006_browser_sessions` revision before web login. The UI
keeps its short-lived access token in memory and uses a rotating HttpOnly cookie to renew it in the
background, including after a page reload. Log in once again after upgrading from the old
session-storage login. Local development should use `localhost` for both apps, not a mix of
`localhost` and `127.0.0.1`, so the browser cookie is sent correctly.

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
- Local result verification and AES-256-GCM wallet export
- Funding amount validation and review
- Security model and help content

## Safety boundary

Simulator workers exercise scheduling but intentionally never return a wallet. A real result requires
the future CUDA executor. Funding remains a clearly labeled simulation because no funding API or
TRON broadcaster has been implemented.

The backend is the wallet custodian in this single-operator design. Protect its encryption key,
database, host, and backups accordingly. Production cryptography and wallet export must receive an
independent security audit before mainnet use.
