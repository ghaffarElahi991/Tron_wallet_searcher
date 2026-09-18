# TronForge API

FastAPI control plane for authenticated, on-demand TRON vanity-wallet generation.

## Current scope

- Single-operator authentication with short-lived JWT access tokens and renewable browser sessions
- Strict validation for the `3x4`, `2x5`, `4x3`, and `2x2` patterns
- Fixed `T` prefix and uppercase-or-`9` enforcement for the next character
- Case-insensitive safe character-set compilation without user-provided regex
- Authenticated job creation, listing, retrieval, idempotency, and cancellation
- FIFO scheduler that gives one active job all configured local GPUs
- One persistent worker process per GPU with non-overlapping leased search ranges
- Worker startup self-tests, progress reporting, cancellation, and restart detection
- Authenticated GPU-fleet status endpoint
- Worker-authenticated candidate submission
- Independent secp256k1, Keccak-256, Base58Check, and pattern verification
- Encrypted server wallet seeds, GPU offsets, and completed private keys at rest
- Idempotent USDT funding requests with encrypted signed-payload persistence
- Separate simulator/live funding processor with solidified-receipt verification
- PostgreSQL migrations and local Docker Compose environment

The API is the wallet custodian in this single-operator architecture. It generates an encrypted base
private key, gives workers only its public point plus non-overlapping offsets, verifies the winning
candidate, combines the final private key on the server, and keeps that result encrypted at rest.

## Local setup

Create the environment file:

```bash
cp .env.example .env
```

Set `TRONFORGE_ADMIN_USERNAME` and `TRONFORGE_ADMIN_PASSWORD` and replace all development secrets
in `.env`. The API creates or updates this one operator account when it starts. Then create a virtual
environment and install the API:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Start PostgreSQL with Docker, apply the migrations, and run the API:

```bash
docker compose up -d database
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`; interactive documentation is at
`http://localhost:8000/docs`.

After updating an existing installation, run `.venv/bin/alembic upgrade head` and restart the API
before opening the web UI. Sign in once to establish the new browser session. Browser access tokens
still expire after 30 minutes, but the UI renews them transparently using an HttpOnly refresh cookie.
The cookie rotates on each renewal and remains valid for 30 days after the last renewal by default;
set `TRONFORGE_BROWSER_SESSION_DAYS` to change that limit. Signing out revokes renewal. Telegram uses
its existing bearer-token reauthentication and does not receive a browser cookie. In production,
serve the web UI and API on same-site HTTPS origins and list the web origin in
`TRONFORGE_CORS_ORIGINS` so the Secure, SameSite cookie can be sent.

If the local PostgreSQL password, role, database ownership, or schema access is out of sync with
`backend/.env`, stop the stack and run this from the repository root:

```bash
./install.sh --database-only
```

The repair preserves all other environment settings, rotates the local `tronforge` password in
PostgreSQL and `backend/.env`, verifies login and permissions, and applies all migrations.

For a no-watcher run, omit `--reload`.

In a second terminal, start two simulated GPU workers:

```bash
TRONFORGE_GENERATOR_MODE=simulator \
TRONFORGE_SIMULATED_GPU_COUNT=2 \
.venv/bin/python -m app.generator
```

The simulator exercises the scheduler, database leases, multiprocessing, progress, and
cancellation paths. It deliberately does not search for or return wallet candidates.

For real local generation, build the native CUDA CLI first, then configure these `.env` values:

```dotenv
TRONFORGE_GENERATOR_MODE=cuda
TRONFORGE_GENERATOR_NATIVE_BINARY=../native/build-cuda/tronforge-generator
TRONFORGE_GENERATOR_CUDA_BENCHMARK_CANDIDATES=67108864
TRONFORGE_GENERATOR_JOB_TIMEOUT_SECONDS=86400
```

Start the real GPU scheduler from the backend directory:

```bash
.venv/bin/python -m app.generator
```

At startup it discovers all NVIDIA devices, benchmarks each one with the native 16-chain kernel,
runs the cryptographic GPU self-test, and publishes the fleet to `GET /api/v1/gpus`. The scheduler
gives one queued job every available GPU. Each worker keeps one native CUDA process and context
alive while streaming bounded search batches to it. The native process also retains its CUDA stream,
events, pinned result memory, device buffers, batch table, and persistent 16-chain point state.
The native worker selects the low-latency batched kernel for short requests and the four-limb,
batch-inversion chained kernel for sustained searches. Python aligns large native batches to the
reported chain count, so each worker's next contiguous range can reuse its GPU point state.
Each Python worker measures its actual end-to-end batch rate, smooths short-term variation, and
adapts the next batch to `TRONFORGE_GENERATOR_KERNEL_BATCH_MS`. The learned rate is written back to
the GPU record so faster devices receive proportionally larger future shards. A reported candidate
is independently reconstructed and verified in Python before the recovered private key is encrypted
in PostgreSQL.

The authenticated job API also returns `observed_rate`: candidates checked divided by elapsed
wall-clock time since the job started. It is `null` until the first completed batch and freezes at
the completed-job average. The web UI and Telegram progress message use their existing polling to
show this value; displaying it adds no CUDA launches, worker messages, database columns, or polling
frequency. It is a job-level average across all assigned GPUs, not the GPU kernel's warm-batch rate.

## USDT funding

Funding is disabled by default. The API persists one funding request per verified wallet and accepts
only amounts from 1 through 1,500 USDT with at most six decimal places. A separate processor prepares
and signs the transaction, stores its exact signed payload encrypted before broadcast, and reconciles
the same transaction ID after timeouts. It never creates a replacement for an uncertain broadcast.

Exercise the complete state machine without moving funds:

```dotenv
TRONFORGE_FUNDING_MODE=simulator
TRONFORGE_FUNDING_NETWORK=nile
```

For live testnet use, configure a deployed six-decimal test TRC-20 contract, node endpoint and a
dedicated test wallet. Prefer a root-readable `0600` key file over an inline environment secret:

```dotenv
TRONFORGE_FUNDING_MODE=live
TRONFORGE_FUNDING_NETWORK=nile
TRONFORGE_FUNDING_CONTRACT_ADDRESS=replace-with-test-token-contract
TRONFORGE_FUNDING_NODE_URL=https://nile.trongrid.io
TRONFORGE_FUNDING_NODE_API_KEY=replace-with-provider-key
TRONFORGE_FUNDING_MASTER_PRIVATE_KEY_FILE=/secure/tronforge-funding.key
TRONFORGE_FUNDING_MASTER_ADDRESS=replace-with-derived-master-address
TRONFORGE_FUNDING_MIN_AVAILABLE_ENERGY=65000
TRONFORGE_FUNDING_MIN_AVAILABLE_BANDWIDTH=350
TRONFORGE_FUNDING_DAILY_LIMIT_USDT=1500
```

Mainnet additionally requires `TRONFORGE_FUNDING_NETWORK=mainnet` and the explicit circuit breaker
`TRONFORGE_FUNDING_ALLOW_MAINNET=true`. Mainnet is pinned to the configured official USDT contract.
The funding wallet must hold enough USDT and TRON Energy/TRX for contract execution. A broadcast
response is not treated as success: the processor waits for a solidified receipt containing the
expected contract, destination and amount in its `Transfer` event.

Run `alembic upgrade head` after updating, then start the stack with `./run.sh`. The launcher starts
the funding processor whenever funding mode is `simulator` or `live`. Telegram funding also requires
`TRONFORGE_TELEGRAM_FUNDING_ENABLED=true`; that option is rejected while public Telegram access is
enabled so group members cannot spend from the master wallet.

## Telegram bot

Create a bot with Telegram's `@BotFather`, then configure the bot token in `.env`:

```dotenv
TRONFORGE_TELEGRAM_BOT_TOKEN=replace-with-botfather-token
TRONFORGE_TELEGRAM_ALLOWED_USER_ID=0
TRONFORGE_TELEGRAM_RESTRICT_USER_ID=true
TRONFORGE_TELEGRAM_ALLOWED_GROUP_ID=0
TRONFORGE_TELEGRAM_PUBLIC_ACCESS=false
TRONFORGE_TELEGRAM_FUNDING_ENABLED=false
TRONFORGE_TELEGRAM_API_URL=http://127.0.0.1:8000/api/v1
TRONFORGE_TELEGRAM_POLL_INTERVAL_SECONDS=3
```

Start the API, generator, and then the Telegram bot in a third backend terminal:

```bash
.venv/bin/python -m app.telegram_bot
```

With default restricted access and the allowed user ID set to `0`, send `/start` to the bot once. It responds with your numeric
Telegram user ID without allowing wallet operations. Put that number in
`TRONFORGE_TELEGRAM_ALLOWED_USER_ID`, restart the bot, and send `/start` again.

For completely open access, set `TRONFORGE_TELEGRAM_PUBLIC_ACCESS=true` and restart the bot. This
overrides both the user-ID restriction and the group allowlist: any human Telegram user can run
wallet commands in a private chat or any group containing the bot. A completed wallet requested in
a group, or revealed from a group, is posted to that group with its address and private key; a
private-chat request is answered privately. All bot users share one backend account, so public users
can also list, cancel, and reveal each other's jobs. This option is deliberately off by default and
should not be used with funded wallets or secrets you need to keep private.

To let every member of one group use the bot, set `TRONFORGE_TELEGRAM_RESTRICT_USER_ID=false` and
restart it. Send `/start` in that group; the bot replies with its chat ID but does not enable wallet
controls yet. Copy that ID into `TRONFORGE_TELEGRAM_ALLOWED_GROUP_ID`, then restart the bot again.
Only this exact group can run wallet commands; other groups and private chats cannot. With the user-ID
restriction left on, only the configured operator ID can use wallet controls in private chat or the
allowlisted group.

In the group, reply directly to the bot's prefix and suffix prompts; the bot requests those replies
so the flow also works with Telegram privacy mode enabled. If a plain `/start` is not delivered in a
busy group, address the command to the bot as `/start@YourBotUsername`.

Every group member should also open the bot privately and send `/start` once. Generation progress
stays in the group, but the verified address and private key are sent only to the requesting member
by direct message. If that DM is unavailable, the group receives an instruction to start a private
chat and use `Recent jobs → Reveal`; the key is never posted to the group. Any member of the
allowlisted group can reveal any ready wallet to their own DM because the backend still uses one
shared operator account. Sending a private key through Telegram or displaying it in a browser
exposes sensitive wallet material; import it promptly, keep an offline backup, and remove any
messages or screenshots.

The bot does not have an idle login timeout. It signs in with the configured operator credentials
before its short-lived API token expires, and reauthenticates once if an unexpected 401 occurs. A
single renewal is shared by concurrent bot requests, so a long-running job monitor continues without
requiring another Telegram command or a browser refresh cookie. If the configured API password or
Telegram bot token is changed, update `.env` and restart the bot.

## Entire local stack with Docker

```bash
docker compose up --build
```

This starts PostgreSQL, the API, and a separate generator service with two simulated GPUs. The
generator waits for the API health check, which also guarantees that migrations have completed.

## Scheduler behavior

Only one generation job can be active on this machine. Jobs are claimed by priority and then FIFO
creation time. Every ready GPU receives a different scalar range for the active job. When a range
is exhausted, the same persistent worker immediately receives another range. The next queued job
does not start until the active job reaches a terminal state and every worker has drained.

Run only one generator process per machine. The process takes an exclusive lock at
`TRONFORGE_GENERATOR_LOCK_FILE` and refuses to start a duplicate instance.

## Test and lint

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

## Public API

- `POST /api/v1/auth/token`
- `POST /api/v1/auth/refresh` (browser cookie)
- `POST /api/v1/auth/logout` (browser cookie)
- `GET /api/v1/auth/me`
- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/gpus`

## Worker API

- `POST /api/v1/internal/results/{job_id}`

The internal result endpoint requires the `X-Worker-API-Key` header. Local GPU work is coordinated
through isolated process queues and UUID lease tokens. The API independently verifies every future
CUDA candidate and derives its final private key before making the result visible to the operator.

## Security notes

- Replace every example secret before any public deployment.
- Back up and protect `TRONFORGE_RESULT_ENCRYPTION_KEY`; losing it makes stored wallet keys unreadable.
- Keep the funding/master-wallet service separate from this API and all GPU workers.
- Terminate TLS at the ingress or reverse proxy.
- Run migrations during deployment rather than creating tables automatically at application start.
- Add production rate limiting at the ingress and application levels before exposing authentication.
- The current owner API returns the decrypted result to the authenticated browser for local
  verification and direct display. Replace this with a one-time delivery grant before public launch.
