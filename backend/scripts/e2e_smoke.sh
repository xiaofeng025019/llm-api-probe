#!/usr/bin/env bash
# End-to-end smoke test: spin uvicorn against a throwaway SQLite, hit the
# main API surfaces, tear everything down. Use this instead of curl-ing
# against your real `data/llm_usability.db` so manual verification never
# pollutes user data.
#
# Usage:  bash scripts/e2e_smoke.sh
#         # or:  make e2e
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMPDIR="$(mktemp -d)"
DB="$TMPDIR/smoke.db"
PORT="${PORT:-18700}"
LOG="$TMPDIR/uvicorn.log"

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

echo "→ starting uvicorn against $DB (port $PORT)"
cd "$HERE"
DATABASE_URL="sqlite+aiosqlite:///$DB" \
SYNC_DATABASE_URL="sqlite:///$DB" \
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning \
  > "$LOG" 2>&1 &
PID=$!

# Wait for /healthz
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! curl -fsS "http://127.0.0.1:$PORT/api/v1/healthz" >/dev/null 2>&1; then
  echo "FAIL: uvicorn never became healthy. Log:"; cat "$LOG"; exit 1
fi

echo "→ running smoke checks"

run() {
  local desc="$1"; shift
  local expected="$1"; shift
  local got
  got=$(curl -sS -o /dev/null -w "%{http_code}" "$@") || got="ERR"
  if [[ "$got" == "$expected" ]]; then
    echo "  ✓ $desc  ($got)"
  else
    echo "  ✗ $desc  (got $got, expected $expected)"
    cat "$LOG"
    exit 1
  fi
}

run "GET /healthz"               200 "http://127.0.0.1:$PORT/api/v1/healthz"
run "GET /readyz"                200 "http://127.0.0.1:$PORT/api/v1/readyz"
run "GET /dashboard"             200 "http://127.0.0.1:$PORT/api/v1/dashboard"
run "GET /providers (empty)"      200 "http://127.0.0.1:$PORT/api/v1/providers"
run "POST /providers"            201 -X POST -H 'Content-Type: application/json' \
    -d '{"name":"t","kind":"openai","base_url":"https://x","api_key":"sk-test"}' \
    "http://127.0.0.1:$PORT/api/v1/providers"
run "GET /providers (1 row)"      200 "http://127.0.0.1:$PORT/api/v1/providers"
run "POST /providers duplicate"   409 -X POST -H 'Content-Type: application/json' \
    -d '{"name":"t","kind":"openai","base_url":"https://x","api_key":"sk-test"}' \
    "http://127.0.0.1:$PORT/api/v1/providers"
run "GET /providers/missing"      404 "http://127.0.0.1:$PORT/api/v1/providers/9999"
run "POST /probe/run"            200 -X POST "http://127.0.0.1:$PORT/api/v1/probe/run?provider_id=1"
run "GET /static / (SPA shell)"   200 "http://127.0.0.1:$PORT/"
run "GET /providers deep link"    200 "http://127.0.0.1:$PORT/providers"
run "GET /api/v1/nonexistent"     404 "http://127.0.0.1:$PORT/api/v1/nonexistent"
run "GET /assets/missing.js"      404 "http://127.0.0.1:$PORT/assets/missing.js"

echo
echo "✓ all smoke checks passed (data cleaned up automatically)"
