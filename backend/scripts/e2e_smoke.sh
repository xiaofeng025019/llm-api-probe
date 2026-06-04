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
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-30}"
CURL_MAX_TIME="${CURL_MAX_TIME:-10}"

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
    kill -INT "$PID" 2>/dev/null || true
    # Bounded wait so a stuck probe can't hang the script past
    # CURL_MAX_TIME * N.
    for _ in {1..15}; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

# Build the frontend if dist is missing — without this, the SPA-route
# assertions (e.g. GET /providers) 404 on a clean checkout. Skip the
# build if SKIP_FRONTEND_BUILD=1 (e.g. a unit-only CI matrix).
if [[ ! -d "$HERE/../frontend/dist" ]]; then
  if [[ "${SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
    echo "→ frontend/dist missing and SKIP_FRONTEND_BUILD=1; SPA assertions will be skipped"
    SKIP_SPA=1
  else
    echo "→ frontend/dist missing; running pnpm build (this may take ~30s)"
    (cd "$HERE/../frontend" && pnpm install --frozen-lockfile=false >/dev/null && pnpm build >/dev/null) \
      || { echo "FAIL: pnpm build failed"; exit 1; }
  fi
else
  SKIP_SPA=0
fi

echo "→ starting uvicorn against $DB (port $PORT)"
cd "$HERE"
DATABASE_URL="sqlite+aiosqlite:///$DB" \
SYNC_DATABASE_URL="sqlite:///$DB" \
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning \
  > "$LOG" 2>&1 &
PID=$!

# Wait for /healthz, with an absolute timeout so the script never hangs.
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SEC ))
healthy=0
while [[ $(date +%s) -lt $deadline ]]; do
  if curl -fsS --max-time "$CURL_MAX_TIME" \
       "http://127.0.0.1:$PORT/api/v1/healthz" >/dev/null 2>&1; then
    healthy=1; break
  fi
  sleep 0.3
done

if [[ $healthy -ne 1 ]]; then
  echo "FAIL: uvicorn never became healthy within ${HEALTH_TIMEOUT_SEC}s. Log:"
  cat "$LOG"
  exit 1
fi

echo "→ running smoke checks"

run() {
  local desc="$1"; shift
  local expected="$1"; shift
  local got
  got=$(curl -sS --max-time "$CURL_MAX_TIME" -o /dev/null -w "%{http_code}" "$@" 2>/dev/null) || got="ERR"
  if [[ "$got" == "$expected" ]]; then
    echo "  ✓ $desc  ($got)"
  else
    echo "  ✗ $desc  (got $got, expected $expected)"
    cat "$LOG"
    exit 1
  fi
}

# Pure-local checks
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
run "GET /api/v1/nonexistent"     404 "http://127.0.0.1:$PORT/api/v1/nonexistent"

# NOTE: deliberately NOT calling POST /probe/run — that fires a real
# HTTP probe to the test base_url (https://x) and the trap's `wait` would
# block on the in-flight httpx call. Verify only that the route exists
# and the validation path works by hitting it with an invalid id (404).
run "POST /probe/run (404 no provider)" 404 -X POST "http://127.0.0.1:$PORT/api/v1/probe/run?provider_id=9999"
run "POST /probe/run (400 missing id)" 400 -X POST "http://127.0.0.1:$PORT/api/v1/probe/run"

# SPA mount requires frontend/dist. Skip if dist wasn't built.
if [[ "${SKIP_SPA:-0}" == "1" ]]; then
  echo "  · (SPA assertions skipped — frontend/dist not built)"
else
  run "GET /static / (SPA shell)"   200 "http://127.0.0.1:$PORT/"
  run "GET /providers deep link"    200 "http://127.0.0.1:$PORT/providers"
  run "GET /assets/missing.js"      404 "http://127.0.0.1:$PORT/assets/missing.js"
fi

echo
echo "✓ all smoke checks passed (data cleaned up automatically)"
