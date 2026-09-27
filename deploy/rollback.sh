#!/usr/bin/env bash
# ============================================================================
# deploy/rollback.sh — restore the immediately previous known-good release
#
# Usage: sudo ./deploy/rollback.sh [TARGET_SHA]
#   TARGET_SHA defaults to the most recent release != current, per the ledger
#   at /var/lib/pdu-control/release-ledger.jsonl (falling back to releases/).
#
# Mutable site config (/etc/pdu-control/config.json) is NOT touched unless a
# matching pre-deploy backup exists for the rollback target (then restored).
# Only restarts pdu-control.service — never reboots the VM.
# ============================================================================
set -Eeuo pipefail

OPT_DIR="/opt/pdu-control"
LEDGER="/var/lib/pdu-control/release-ledger.jsonl"
BACKUP_DIR="/var/backups/pdu-control"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ $EUID -ne 0 ]]; then echo "FATAL: run as root" >&2; exit 1; fi

CURRENT_SHA="$(basename "$(readlink -f "$OPT_DIR/current" 2>/dev/null)" 2>/dev/null || echo "")"
TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
  if [[ -f "$LEDGER" ]]; then
    TARGET="$(grep '"event":"deployed"' "$LEDGER" | python3 -c "
import json, sys
shas = [json.loads(l)['release'] for l in sys.stdin if l.strip()]
prev = [s for s in shas if s != '$CURRENT_SHA']
print(prev[-1] if prev else '')")"
  fi
  if [[ -z "$TARGET" ]]; then
    # fall back: newest release dir that isn't current
    TARGET="$(ls -1t "$OPT_DIR/releases" 2>/dev/null | grep -v "^$CURRENT_SHA\$" | head -1 || true)"
  fi
fi

if [[ -z "$TARGET" ]]; then
  echo "FATAL: no prior release found to roll back to" >&2
  exit 2
fi
if [[ ! -d "$OPT_DIR/releases/$TARGET" ]]; then
  echo "FATAL: release dir missing: $OPT_DIR/releases/$TARGET" >&2
  exit 2
fi

echo "rollback: $CURRENT_SHA -> $TARGET"
ln -sfn "$OPT_DIR/releases/$TARGET" "$OPT_DIR/current"

# restore config backup taken before the failed deploy (most recent)
LATEST_CFG_BAK="$(ls -1t "$BACKUP_DIR"/config-*.json 2>/dev/null | head -1 || true)"
if [[ -n "$LATEST_CFG_BAK" ]]; then
  cp -a "$LATEST_CFG_BAK" /etc/pdu-control/config.json
  echo "config restored from: $LATEST_CFG_BAK"
else
  echo "config left untouched (no backup found)"
fi

rsync -a "$OPT_DIR/releases/$TARGET/app/" "$OPT_DIR/" --exclude venv --exclude '__pycache__'
chown -R pducontrol:pducontrol "$OPT_DIR"

systemctl restart pdu-control
sleep 3
if "$(dirname "$0")/healthcheck.sh" http://127.0.0.1:5000 app; then
  echo "{\"ts\":\"$TS\",\"release\":\"$TARGET\",\"event\":\"rollback\",\"result\":\"accepted\",\"from\":\"$CURRENT_SHA\"}" >> "$LEDGER"
  echo "ROLLBACK_OK ($TARGET)"
else
  echo "ROLLBACK_DEGRADED — health still failing, manual intervention required" >&2
  exit 4
fi