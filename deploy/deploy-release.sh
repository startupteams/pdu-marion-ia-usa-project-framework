#!/usr/bin/env bash
# ============================================================================
# deploy/deploy-release.sh — transactional release deploy with auto-rollback
#
# Usage: sudo ./deploy/deploy-release.sh /path/to/pdu-manager-<sha>.tar.gz
#
# Transaction: backup config → install release → validate → restart service →
# health-gate → commit, or on failure: restore config backup + prior release
# state + service restart.
# NEVER actuates PDUs; never reboots the VM; only restarts pdu-control.service.
# ============================================================================
set -Eeuo pipefail

ARTIFACT="${1:?usage: deploy-release.sh <artifact.tar.gz>}"
APP_USER="pducontrol"
OPT_DIR="/opt/pdu-control"
ETC_DIR="/etc/pdu-control"
BACKUP_DIR="/var/backups/pdu-control"
LEDGER="/var/lib/pdu-control/release-ledger.jsonl"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ $EUID -ne 0 ]]; then echo "FATAL: run as root" >&2; exit 1; fi
if [[ ! -f "$ARTIFACT" ]]; then echo "FATAL: artifact not found: $ARTIFACT" >&2; exit 1; fi

verify_checksum() {
  local dir sha file
  dir="$(dirname "$ARTIFACT")"
  sha_file="$dir/$(basename "$ARTIFACT" .tar.gz).sha256"
  if [[ -f "$sha_file" ]]; then
    # sha256sum -c needs the basename (sidecars may carry absolute build paths)
    local expect actual
    expect="$(awk '{print $1}' "$sha_file")"
    actual="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
    if [[ "$expect" != "$actual" ]]; then
      echo "FATAL: artifact checksum mismatch (expected $expect, got $actual)" >&2
      exit 2
    fi
    echo "checksum verified: $actual"
  else
    echo "WARN: no checksum sidecar for artifact — continuing (verify source!)"
  fi
}

rollback() {
  local stage="$1"
  echo "!! ROLLBACK from stage: $stage" >&2
  if [[ -n "${PREV_RELEASE_SHA:-}" && -d "$OPT_DIR/releases/$PREV_RELEASE_SHA" ]]; then
    ln -sfn "$OPT_DIR/releases/$PREV_RELEASE_SHA" "$OPT_DIR/current"
    # restore the PREVIOUS release's code into the live tree (the switch step
    # already copied the broken code over /opt/pdu-control; the symlink alone
    # does not undo that)
    if [[ -d "$OPT_DIR/releases/$PREV_RELEASE_SHA/app" ]]; then
      rsync -a "$OPT_DIR/releases/$PREV_RELEASE_SHA/app/" "$OPT_DIR/" --exclude venv --exclude '__pycache__'
    else
      rsync -a "$OPT_DIR/releases/$PREV_RELEASE_SHA/" "$OPT_DIR/" --exclude venv --exclude '__pycache__' --exclude deploy --exclude docs
    fi
  fi
  if [[ -f "$BACKUP_DIR/config-$TS.json" ]]; then
    cp -a "$BACKUP_DIR/config-$TS.json" "$ETC_DIR/config.json"
    echo "config restored from backup"
  fi
  systemctl restart pdu-control || true
  sleep 3
  if "$(dirname "$0")/healthcheck.sh" http://127.0.0.1:5000 app >/dev/null 2>&1; then
    echo "ROLLBACK_OK"
  else
    echo "ROLLBACK_DEGRADED — manual intervention required" >&2
  fi
  exit 4
}

trap 'rollback mid-failure' ERR

echo "== [1/8] preflight =="
verify_checksum
systemctl is-active pdu-control >/dev/null || true
PREV_RELEASE_SHA="$(readlink -f "$OPT_DIR/current" 2>/dev/null | xargs basename 2>/dev/null || true)"
PREV_RELEASE_SHA="${PREV_RELEASE_SHA:-}"
echo "previous release: ${PREV_RELEASE_SHA:-none}"

echo "== [2/8] backup mutable config =="
mkdir -p "$BACKUP_DIR"
cp -a "$ETC_DIR/config.json" "$BACKUP_DIR/config-$TS.json"
sha256sum "$BACKUP_DIR/config-$TS.json" > "$BACKUP_DIR/config-$TS.json.sha256"

echo "== [3/8] stage + verify payload =="
STAGE="$(mktemp -d /tmp/pdu-deploy-XXXXXX)"
trap 'rm -rf "$STAGE" 2>/dev/null || true' EXIT
tar -xzf "$ARTIFACT" -C "$STAGE"
PAYLOAD="$(find "$STAGE" -maxdepth 1 -type d -name 'pdu-manager-*' | head -1)"
RELEASE_SHA="$(basename "$PAYLOAD" | sed 's/^pdu-manager-//')"
echo "release sha: $RELEASE_SHA"
echo "== [4/8] install into $OPT_DIR/releases/$RELEASE_SHA =="
mkdir -p "$OPT_DIR/releases"
rm -rf "$OPT_DIR/releases/$RELEASE_SHA.tmp"
if [[ -d "$PAYLOAD/app" ]]; then
  cp -a "$PAYLOAD/app" "$OPT_DIR/releases/$RELEASE_SHA.tmp"
else
  # payload variant: modules directly under the release root (no app/ subdir)
  mkdir -p "$OPT_DIR/releases/$RELEASE_SHA.tmp/app"
  for f in "$PAYLOAD"/*.py; do
    [[ -e "$f" ]] && cp -a "$f" "$OPT_DIR/releases/$RELEASE_SHA.tmp/app/"
  done
fi
cp -a "$PAYLOAD/requirements.txt" "$OPT_DIR/releases/$RELEASE_SHA.tmp/" 2>/dev/null || true
cp -a "$PAYLOAD/deploy" "$OPT_DIR/releases/$RELEASE_SHA.tmp/" 2>/dev/null || true
cp -a "$PAYLOAD/docs" "$OPT_DIR/releases/$RELEASE_SHA.tmp/" 2>/dev/null || true
mv "$OPT_DIR/releases/$RELEASE_SHA.tmp" "$OPT_DIR/releases/$RELEASE_SHA"
ln -sfn "$OPT_DIR/releases/$RELEASE_SHA" "$OPT_DIR/current"

echo "== [5/8] venv deps (idempotent install into shared venv) =="
pip_install_log="$(mktemp)"
if ! "$OPT_DIR/venv/bin/pip" install --quiet -r "$OPT_DIR/releases/$RELEASE_SHA/requirements.txt" > "$pip_install_log" 2>&1; then
  echo "pip install failed:" >&2; tail -5 "$pip_install_log" >&2
  rollback "deps"
fi
rm -f "$pip_install_log"

echo "== [6/8] validate config (no service start yet) =="
python3 -c "import json; json.load(open('$ETC_DIR/config.json'))" || rollback "config-validate"

echo "== [7/8] switch code into live tree + restart service =="
if [[ -d "$OPT_DIR/releases/$RELEASE_SHA/app" ]]; then
  rsync -a "$OPT_DIR/releases/$RELEASE_SHA/app/" "$OPT_DIR/" --exclude venv --exclude '__pycache__'
else
  rsync -a "$OPT_DIR/releases/$RELEASE_SHA/" "$OPT_DIR/" --exclude venv --exclude '__pycache__' --exclude deploy --exclude docs
fi
chown -R "$APP_USER:$APP_USER" "$OPT_DIR"
systemctl restart pdu-control
sleep 3

echo "== [8/8] health gate =="
if "$(dirname "$0")/healthcheck.sh" http://127.0.0.1:5000 app; then
  echo "release ACCEPTED: $RELEASE_SHA"
  echo "{\"ts\":\"$TS\",\"release\":\"$RELEASE_SHA\",\"event\":\"deployed\",\"result\":\"accepted\"}" >> "$LEDGER"
else
  echo "health FAILED — auto-rollback" >&2
  rollback "health-gate"
fi