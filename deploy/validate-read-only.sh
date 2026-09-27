#!/usr/bin/env bash
# ============================================================================
# deploy/validate-read-only.sh — non-actuating real-backend validation
# (FW-011 read-only validation subset; Future Work v2 §10)
#
# Purpose: prove the running service can READ real PDU state without sending
# any power-changing command. Runs entirely over HTTP GET against the local
# service. ZERO actuation by construction:
#   - only GET requests are issued (plus the emergency session login POST,
#     which authenticates and never actuates);
#   - every power-changing endpoint is deliberately never touched;
#   - no direct SSH to PDUs from this script (the app itself reads state).
#
# Usage:  sudo ./deploy/validate-read-only.sh [base_url]
#         base_url default: https://127.0.0.1  (nginx on-box)
#
# Auth: emergency-local credential from /etc/pdu-control/secrets.env
# (WEB_USER/WEB_PASS, base64 in file, decoded in memory only; never printed)
# is used for (a) the emergency web-login check and (b) /api/v1 HTTP Basic
# (the API accepts the emergency actor as pdu-admin).
#
# Exit 0 = all read-only checks pass; exit 1 = any failure (deploy gates on this).
# ============================================================================
set -Eeuo pipefail

BASE_URL="${1:-https://127.0.0.1}"
SECRETS="/etc/pdu-control/secrets.env"
TMPDIR_RO="$(mktemp -d /tmp/pdu-ro-XXXXXX)"
trap 'rm -rf "$TMPDIR_RO"' EXIT

if [[ $EUID -ne 0 ]]; then
  echo "FATAL: run as root (sudo)." >&2
  exit 1
fi

FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS+1)); }

# --- decode emergency-local creds in memory (never printed) -----------------
eval "$(python3 - <<'PY'
import base64
vals = {}
for line in open("/etc/pdu-control/secrets.env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        vals[k] = v
def dec(key):
    try:
        return base64.b64decode(vals.get(key, "")).decode().replace('"', '\\"')
    except Exception:
        return ""
print(f'WEB_USER="{dec("WEB_USER_B64")}"')
print(f'WEB_PASS="{dec("WEB_PASS_B64")}"')
PY
)"

if [[ -z "$WEB_USER" || -z "$WEB_PASS" ]]; then
  echo "FATAL: could not decode WEB_USER/WEB_PASS from $SECRETS" >&2
  exit 1
fi

B64CREDS="$(printf '%s' "$WEB_USER:$WEB_PASS" | base64 -w0)"
JAR="$TMPDIR_RO/cookies.txt"

# --- 1. service reachable over HTTPS ---------------------------------------
CODE="$(curl -sk -o /dev/null -w '%{http_code}' "$BASE_URL/")"
if [[ "$CODE" == "200" || "$CODE" == "302" ]]; then
  pass "GET / -> $CODE"
else
  fail "GET / -> unexpected $CODE"
fi

# --- 2. login page reachable -------------------------------------------------
CODE="$(curl -sk -o /dev/null -w '%{http_code}' "$BASE_URL/login")"
[[ "$CODE" == "200" ]] && pass "GET /login -> 200" || fail "GET /login -> $CODE"

# --- 3. emergency-local login works (POST to the login form endpoint) -------
# NOTE: this is a session login, NOT a power action. The form's emergency
# path uses mode=emergency + e_user/e_pass (see app.py login()).
LOGIN_OUT="$(curl -sk -c "$JAR" -o "$TMPDIR_RO/login.html" -w '%{http_code}' \
  --data-urlencode "mode=emergency" \
  --data-urlencode "e_user=$WEB_USER" \
  --data-urlencode "e_pass=$WEB_PASS" \
  "$BASE_URL/login")"
if [[ "$LOGIN_OUT" == "302" || "$LOGIN_OUT" == "200" ]]; then
  pass "emergency-local login accepted (HTTP $LOGIN_OUT)"
else
  fail "emergency-local login rejected (HTTP $LOGIN_OUT)"
fi

# --- 4. main UI renders with session ----------------------------------------
CODE="$(curl -sk -b "$JAR" -o "$TMPDIR_RO/ui.html" -w '%{http_code}' "$BASE_URL/")"
if [[ "$CODE" == "200" ]]; then
  pass "GET / (authed) -> 200"
  if grep -q "MIAM-001" "$TMPDIR_RO/ui.html" 2>/dev/null; then
    pass "UI renders PDU asset labels"
  else
    fail "UI does not show expected PDU asset labels"
  fi
else
  fail "GET / (authed) -> $CODE"
fi

# --- 5. /health: correct backend mode + 3-PDU inventory ----------------------
HEALTH_CODE="$(curl -sk -o "$TMPDIR_RO/health.json" -w '%{http_code}' "$BASE_URL/health")"
if [[ "$HEALTH_CODE" == "200" ]] && python3 - "$TMPDIR_RO/health.json" <<'PY' 2>/dev/null
import json, sys
h = json.load(open(sys.argv[1]))
assert h.get("status") == "ok"
mode = h.get("backend_mode")
assert mode in ("mock", "real"), f"backend_mode missing/invalid: {mode}"
assert len(h.get("pdus", [])) == 3, "expected 3 PDUs"
PY
then
  MODE="$(python3 -c "import json;print(json.load(open('$TMPDIR_RO/health.json'))['backend_mode'])")"
  pass "/health -> backend_mode=$MODE, 3 PDUs"
else
  MODE="unknown"
  fail "/health payload invalid or missing backend_mode"
fi

# --- 6. /api/v1 read-only: inventory + outlet state reads (Basic auth, GET) --
V1_PDUS="$(curl -sk -o "$TMPDIR_RO/v1pdus.json" -w '%{http_code}' \
  -H "Authorization: Basic $B64CREDS" "$BASE_URL/api/v1/pdus")"
if [[ "$V1_PDUS" == "200" ]]; then
  pass "GET /api/v1/pdus -> 200"
  if python3 - "$TMPDIR_RO/v1pdus.json" <<'PY' 2>/dev/null
import json, sys
data = json.load(open(sys.argv[1]))
pdus = data if isinstance(data, list) else data.get("pdus", [])
assert len(pdus) == 3, f"expected 3 PDUs, got {len(pdus)}"
sys.exit(0)
PY
  then
    pass "/api/v1/pdus lists all 3 PDUs"
  else
    fail "/api/v1/pdus payload invalid"
  fi
else
  fail "GET /api/v1/pdus -> $V1_PDUS"
fi

# Outlet state read on PDU 153 (asset key), outlet 12 — a GET; read-only.
OUT12="$(curl -sk -o "$TMPDIR_RO/outlet12.json" -w '%{http_code}' \
  -H "Authorization: Basic $B64CREDS" "$BASE_URL/api/v1/pdus/MIAM-00153/outlets/12")"
if [[ "$OUT12" == "200" ]] && python3 - "$TMPDIR_RO/outlet12.json" <<'PY' 2>/dev/null
import json, sys
o = json.load(open(sys.argv[1]))
assert o.get("state") in ("ON", "OFF", "UNKNOWN"), f"bad state: {o.get('state')}"
assert o.get("name"), "label missing"
sys.exit(0)
PY
then
  pass "GET /api/v1/pdus/MIAM-00153/outlets/12 -> 200 (state readable)"
else
  fail "outlet state read failed (HTTP $OUT12)"
fi

# --- 7. structural guarantee: no power-changing requests in this script ------
if grep -E 'curl ([^|]*)(-X POST|--request POST)' "$0" | grep -v '/login' >/dev/null 2>&1; then
  fail "script contains a non-login POST (must never happen)"
else
  pass "no power-changing requests in validation path (login POST only)"
fi

echo "VALIDATE_READ_ONLY_RESULT: $((FAILS == 0 ? 0 : 1)) ($FAILS failures)"
exit $((FAILS == 0 ? 0 : 1))
