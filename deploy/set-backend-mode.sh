#!/usr/bin/env bash
# ============================================================================
# deploy/set-backend-mode.sh — switch the PDU Manager backend (mock | real)
#
# Usage:
#   sudo ./deploy/set-backend-mode.sh mock    # CI/staging: no hardware reachable
#   sudo ./deploy/set-backend-mode.sh real    # PRODUCTION: real PowerAlert SSH driver
#   sudo ./deploy/set-backend-mode.sh status  # show current mode (no change)
#
# Mechanism (FW-003/FW-004, Future Work v2):
#   The source of truth for backend selection is the drop-in
#   /etc/systemd/system/pdu-control.service.d/backend-mode.conf
#   which sets Environment=PDU_BACKEND=<mode>. The app reads PDU_BACKEND at
#   import time; in mock mode the real SSH driver is NEVER imported, so no
#   code path can reach hardware (REQ-010). A machine-readable copy of the
#   current mode is written to /etc/pdu-control/backend_mode.json for
#   healthcheck/CI/monitoring consumption.
#
# Safety:
#   - Requires explicit mode argument; refuses anything but mock|real.
#   - Refuses to switch to 'real' when /etc/pdu-control/secrets.env is missing
#     or still contains STAGING throwaway values (fail-safe against deploying
#     a production service with placeholder credentials).
#   - Mock→real / real→mock both require a service restart (performed here);
#     the restart is application-only, never a VM reboot.
#   - Never touches /etc/pdu-control/config.json or secrets.env contents.
#
# This script ACTUATES NOTHING. It changes which driver the app would use.
# ============================================================================
set -Eeuo pipefail

MODE="${1:-}"
ETC_DIR="/etc/pdu-control"
DROPIN_DIR="/etc/systemd/system/pdu-control.service.d"
DROPIN="${DROPIN_DIR}/backend-mode.conf"
STATE_FILE="${ETC_DIR}/backend_mode.json"
SECRETS="${ETC_DIR}/secrets.env"

usage() {
  echo "Usage: $0 <mock|real|status>" >&2
  exit 2
}

case "$MODE" in
  mock|real|status) ;;
  *) usage ;;
esac

if [[ $EUID -ne 0 ]]; then
  echo "FATAL: run as root (sudo)." >&2
  exit 1
fi

current_mode() {
  # Scan ALL drop-ins for PDU_BACKEND (staging installs may have used other
  # drop-in filenames, e.g. mock-backend.conf). Last match wins; the app's
  # default when nothing sets the var is "real".
  local found
  found="$(grep -rhoP 'Environment=PDU_BACKEND=\K\w+' "$DROPIN_DIR" 2>/dev/null | tail -1)"
  if [[ -n "$found" ]]; then
    echo "$found"
  else
    echo "real"   # app default when no drop-in sets PDU_BACKEND
  fi
}

write_state_file() {
  local mode="$1"
  mkdir -p "$ETC_DIR"
  cat > "$STATE_FILE" <<EOF
{
  "backend_mode": "${mode}",
  "changed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "changed_by": "set-backend-mode.sh",
  "note": "machine-readable copy; authoritative source is backend-mode.conf drop-in"
}
EOF
  chmod 644 "$STATE_FILE"
}

if [[ "$MODE" == "status" ]]; then
  CUR="$(current_mode)"
  echo "backend_mode: ${CUR}"
  write_state_file "$CUR" >/dev/null 2>&1 || true
  exit 0
fi

CUR="$(current_mode)"
if [[ "$CUR" == "$MODE" ]]; then
  echo "Already in ${MODE} mode — no change (state file refreshed)."
  write_state_file "$MODE"
  exit 0
fi

if [[ "$MODE" == "real" ]]; then
  # FAIL-SAFE: refuse to activate the real backend without production secrets.
  if [[ ! -f "$SECRETS" ]]; then
    echo "FATAL: $SECRETS missing — cannot activate real backend." >&2
    exit 1
  fi
  if grep -q "stage-pass-not-a-real-secret\|STAGING-ONLY" "$SECRETS" 2>/dev/null; then
    echo "FATAL: secrets.env still contains STAGING throwaway values — refusing to activate real backend." >&2
    echo "       Provision production secrets first (docs/DEPLOYMENT.md §secrets)." >&2
    exit 1
  fi
  echo "NOTE: activating the REAL backend. Validation must be read-only only"
  echo "      (no ON/OFF/REBOOT/CYCLE to physical outlets)."
fi

mkdir -p "$DROPIN_DIR"
# Remove any OTHER drop-in that sets PDU_BACKEND (e.g. legacy staging
# mock-backend.conf from install.sh) — systemd applies drop-ins in filename
# order and the LAST one would silently win.
for other in "$DROPIN_DIR"/*.conf; do
  if [[ -f "$other" && "$other" != "$DROPIN" ]] && grep -q "PDU_BACKEND" "$other" 2>/dev/null; then
    rm -f "$other"
    echo "  removed legacy drop-in: $(basename "$other")"
  fi
done
cat > "$DROPIN" <<EOF
# Managed by deploy/set-backend-mode.sh (FW-003/FW-004). Manual edits will be
# overwritten by the next mode switch; use the script, not this file.
[Service]
Environment=PDU_BACKEND=${MODE}
EOF

systemctl daemon-reload
systemctl restart pdu-control

# verify the service came back
sleep 2
if ! systemctl is-active --quiet pdu-control; then
  echo "FATAL: pdu-control failed to restart after backend switch — inspect journalctl -u pdu-control" >&2
  exit 1
fi

write_state_file "$MODE"
echo "backend_mode: ${CUR} -> ${MODE} (service restarted, active)"
