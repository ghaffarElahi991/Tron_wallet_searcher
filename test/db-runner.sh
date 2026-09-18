#!/usr/bin/env bash
# Verify the TronForge PostgreSQL configuration and API database readiness.
set -Eeuo pipefail
umask 077

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${TEST_DIR}/.." && pwd -P)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
UVICORN_BIN="${BACKEND_DIR}/.venv/bin/uvicorn"
API_PORT="${TRONFORGE_DB_TEST_PORT:-18000}"
API_PID=""
API_LOG=""

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'Database test error: %s\n' "$1" >&2; exit 1; }
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill -TERM "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_LOG" && -f "$API_LOG" ]]; then
    rm -f -- "$API_LOG"
  fi
}
show_api_failure() {
  if [[ -n "$API_LOG" && -s "$API_LOG" ]]; then
    printf '\n--- API test log ---\n' >&2
    sed -n '1,240p' "$API_LOG" >&2
    printf '%s\n' '--- end API test log ---' >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $# -eq 0 ]] || fail "Usage: ./test/db-runner.sh"
[[ "$API_PORT" =~ ^[0-9]+$ ]] || fail "TRONFORGE_DB_TEST_PORT must be a number."
(( API_PORT >= 1024 && API_PORT <= 65535 )) || \
  fail "TRONFORGE_DB_TEST_PORT must be between 1024 and 65535."
[[ -f "${BACKEND_DIR}/.env" ]] || fail "backend/.env is missing."
[[ -x "$VENV_PYTHON" ]] || fail "backend/.venv Python is missing."
[[ -x "$UVICORN_BIN" ]] || fail "Uvicorn is missing from backend/.venv."
command -v pg_isready >/dev/null 2>&1 || fail "pg_isready is missing."
command -v curl >/dev/null 2>&1 || fail "curl is missing."

if [[ -v TRONFORGE_DATABASE_URL ]]; then
  printf 'Ignoring inherited TRONFORGE_DATABASE_URL; backend/.env is authoritative.\n' >&2
  unset TRONFORGE_DATABASE_URL
fi

step "Check PostgreSQL TCP availability"
if ! pg_isready -h localhost -p 5432; then
  fail "PostgreSQL is not accepting connections on localhost:5432; run ./test/db-install.sh first."
fi

step "Verify credentials, permissions, schema, and migration revision"
(cd "$BACKEND_DIR" && "$VENV_PYTHON" - <<'PY'
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings

settings = get_settings()
url = make_url(settings.database_url)
if url.drivername != "postgresql+psycopg":
    raise SystemExit("Expected a postgresql+psycopg URL in backend/.env.")

required_tables = {
    "alembic_version",
    "auth_sessions",
    "funding_transactions",
    "generation_jobs",
    "generation_results",
    "generation_shards",
    "gpu_devices",
    "users",
}

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
try:
    with engine.connect() as connection:
        identity = connection.execute(
            text(
                "SELECT current_user, current_database(), "
                "pg_get_userbyid((SELECT datdba FROM pg_database "
                "WHERE datname = current_database())), "
                "pg_get_userbyid((SELECT nspowner FROM pg_namespace "
                "WHERE nspname = 'public')), "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user), "
                "(SELECT rolcanlogin FROM pg_roles WHERE rolname = current_user), "
                "has_database_privilege(current_user, current_database(), 'CONNECT'), "
                "has_schema_privilege(current_user, 'public', 'USAGE'), "
                "has_schema_privilege(current_user, 'public', 'CREATE')"
            )
        ).one()
        (
            current_user,
            database_name,
            database_owner,
            schema_owner,
            is_superuser,
            can_login,
            can_connect,
            can_use,
            can_create,
        ) = identity
        local_target = (
            url.host in {"localhost", "127.0.0.1", "::1"}
            and (url.port or 5432) == 5432
            and url.username == "tronforge"
            and url.database == "tronforge"
        )
        if local_target and (database_owner != current_user or schema_owner != current_user):
            raise SystemExit(
                "The local tronforge role does not own both the database and public schema."
            )
        if is_superuser or not can_login:
            raise SystemExit("The configured application role has unsafe or invalid role flags.")
        if not all((can_connect, can_use, can_create)):
            raise SystemExit(
                "The configured role lacks required database or public-schema privileges."
            )

        migration_context = MigrationContext.configure(connection)
        current_revision = migration_context.get_current_revision()
        alembic_config = Config("alembic.ini")
        migration_script = ScriptDirectory.from_config(alembic_config)
        migration_heads = migration_script.get_heads()
        if len(migration_heads) != 1:
            raise SystemExit(f"Expected one Alembic head, found: {migration_heads}")
        if current_revision != migration_heads[0]:
            raise SystemExit(
                f"Database migration is {current_revision!r}; expected {migration_heads[0]!r}."
            )

        tables = set(inspect(connection).get_table_names(schema="public"))
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise SystemExit(f"Required database tables are missing: {', '.join(missing_tables)}")

        connection.execute(
            text("CREATE TEMP TABLE tronforge_database_probe (value integer NOT NULL)")
        )
        connection.execute(text("INSERT INTO tronforge_database_probe (value) VALUES (1)"))
        probe_value = connection.execute(
            text("SELECT value FROM tronforge_database_probe")
        ).scalar_one()
        if probe_value != 1:
            raise SystemExit("Temporary database write/read probe returned an unexpected value.")
except SQLAlchemyError as exc:
    raise SystemExit(f"Database connection or query failed: {exc}") from None

print(f"Database host: {url.host}:{url.port or 5432}")
print(f"Authenticated role: {current_user}")
print(f"Database: {database_name} (owner: {database_owner})")
print(f"Public schema owner: {schema_owner}")
print(f"Alembic revision: {current_revision} (head)")
print(f"Required tables: {len(required_tables)} present")
print("Temporary write/read probe: passed")
engine.dispose()
PY
)

step "Check that the isolated API test port is free"
"$VENV_PYTHON" - "$API_PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(f"127.0.0.1:{port} is unavailable: {exc}") from exc
PY

step "Start a temporary API and test database readiness"
API_LOG="$(mktemp /tmp/tronforge-db-api.XXXXXX.log)"
(cd "$BACKEND_DIR" && exec env PYTHONUNBUFFERED=1 "$UVICORN_BIN" app.main:app \
  --host 127.0.0.1 --port "$API_PORT" --log-level warning) >"$API_LOG" 2>&1 &
API_PID=$!

api_ready=0
for ((attempt = 0; attempt < 30; attempt++)); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${API_PORT}/health/ready" >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    show_api_failure
    fail "The temporary API exited before becoming ready."
  fi
  sleep 1
done
if [[ "$api_ready" -ne 1 ]]; then
  show_api_failure
  fail "The temporary API did not become database-ready within 30 seconds."
fi

curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/health/live" >/dev/null
curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/health/ready" >/dev/null

kill -TERM "$API_PID" 2>/dev/null || true
wait "$API_PID" 2>/dev/null || true
API_PID=""

step "All database and API checks passed"
printf 'PostgreSQL, credentials, permissions, migrations, tables, and API readiness are healthy.\n'
