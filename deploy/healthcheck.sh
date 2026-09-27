#!/usr/bin/env bash
# ============================================================================
# deploy/healthcheck.sh — read-only health checks (NO PDU actuation, ever)
#
# Usage: ./deploy/healthcheck.sh [BASE_URL] [MODE]
#   BASE_URL  default http://127.0.0.1:5000 (localhost app probe)
#   MODE      app (default) | https | full
#
# Exit 0 = healthy; nonzero = failed (gates deploy/rollback).
# ============================================================================
set -uo pipefail

BASE_URL="${1:-http://127.0.0.1:5000}"
MODE="${2:-app}"
FAILS=0

check() {
  local name="$1" shift_args=("$@")
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $name"
  else
    echo "FAIL  $name"
    FAILS=$((FAILS+1))
  fi
}

# 1. service running
check "service active" systemctl is-active pdu-control

# 2. app /health
check "app /health" curl -fsS --max-time 10 "$BASE_URL/health"

# 3. unauth gates
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/" || echo 000)
if [[ "$code" == "302" || "$code" == "401" ]]; then
  echo "PASS  / unauth gate ($code)"
else
  echo "FAIL  / unauth gate ($code)"
  FAILS=$((FAILS+1))
fi

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/api/v1/pdus" || echo 000)
if [[ "$code" == "401" ]]; then
  echo "PASS  /api/v1/pdus unauth 401"
else
  echo "FAIL  /api/v1/pdus unauth ($code — expected 401)"
  FAILS=$((FAILS+1))
fi

# 4. login page renders
check "login page" curl -fsS --max-time 10 "$BASE_URL/login"

if [[ "$MODE" == "https" || "$MODE" == "full" ]]; then
  HB="${BASE_URL/5000/}"
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "https://127.0.0.1/health" || echo 000)
  if [[ "$code" == "200" ]]; then echo "PASS  https /health via proxy"; else echo "FAIL  https /health ($code)"; FAILS=$((FAILS+1)); fi
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "https://127.0.0.1/login" || echo 000)
  if [[ "$code" == "200" ]]; then echo "PASS  https /login via proxy"; else echo "FAIL  https /login ($code)"; FAILS=$((FAILS+1)); fi
fi

if [[ "$MODE" == "full" ]]; then
  # config parses
  check "config JSON valid" python3 -c "import json; json.load(open('/etc/pdu-control/config.json'))"
  # no new fatal errors in the last 20 journald lines
  if journalctl -u pdu-control -n 20 --no-pager 2>/dev/null | grep -qiE 'Traceback|CRITICAL'; then
    echo "FAIL  recent logs contain fatal errors"
    FAILS=$((FAILS+1))
  else
    echo "PASS  no recent fatal errors"
  fi
  # mock backend sanity when enabled (read-only, in-process)
  if [[ "${PDU_BACKEND:-}" == "mock" ]]; then
    echo "PASS  mock backend mode (no hardware reachable)"
  fi
fi

echo "HEALTHCHECK_RESULT: $((FAILS == 0 ? 0 : 1)) ($FAILS failures)"
exit $((FAILS == 0 ? 0 : 1))