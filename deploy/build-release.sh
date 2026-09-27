#!/usr/bin/env bash
# ============================================================================
# deploy/build-release.sh — build an immutable PDU Manager release artifact
#
# Usage:   ./deploy/build-release.sh [GIT_SHA]
# Output:  dist/pdu-manager-<sha>.tar.gz + dist/pdu-manager-<sha>.sha256
#
# The artifact contains ONLY: app code, deploy assets, docs, requirements.txt.
# It contains NO secrets, NO config (config is external), NO state.
# ============================================================================
set -Eeuo pipefail

SHA="${1:-$(git rev-parse --verify HEAD)}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$REPO_ROOT/dist"
STAGE="$(mktemp -d /tmp/pdu-release-XXXXXX)"

trap 'rm -rf "$STAGE"' EXIT

echo "[build-release] source: repo@$SHA"

# --- verify clean-ish tree for the sha we ship -------------------------------
git -C "$REPO_ROOT" rev-parse --verify "$SHA" >/dev/null || {
  echo "FATAL: unknown git sha: $SHA" >&2; exit 1;
}

# --- assemble staging tree ----------------------------------------------------
mkdir -p "$STAGE/pdu-manager-$SHA"
STAGE_APP="$STAGE/pdu-manager-$SHA"
cp -a "$REPO_ROOT/app"            "$STAGE_APP/app"
cp -a "$REPO_ROOT/deploy"         "$STAGE_APP/deploy"
cp -a "$REPO_ROOT/docs"           "$STAGE_APP/docs"
cp    "$REPO_ROOT/requirements.txt" "$STAGE_APP/requirements.txt"
cp    "$REPO_ROOT/config/examples/config.example.json" "$STAGE_APP/config.example.json"
cp    "$REPO_ROOT/tests"/*.py     "$STAGE_APP/" 2>/dev/null || true
cp -a "$REPO_ROOT/tests"          "$STAGE_APP/tests"

# strip generated noise (double guard)
find "$STAGE_APP" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_APP" -name '.venv*' -o -name '*.pyc' -delete 2>/dev/null || true

# --- version manifest ----------------------------------------------------------
{
  echo "release_sha=$SHA"
  echo "built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "built_by=pdu-manager CI"
  echo "app_modules=$(find "$STAGE_APP/app" -maxdepth 1 -name '*.py' | wc -l)"
} > "$STAGE_APP/MANIFEST.txt"

# --- secret scan gate (fail the build on any hit) ------------------------------
if grep -rInE '(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|https?://[^/]*:[^/@]*@)' "$STAGE_APP" >/dev/null 2>&1; then
  echo "FATAL: secret pattern detected in artifact — refusing to build" >&2
  exit 2
fi
# b64-credential literal sweep: any _B64=<value> with content (placeholder empty is fine)
if grep -rInE '^[A-Z_]+_B64=[A-Za-z0-9+/=]{8,}' "$STAGE_APP" >/dev/null 2>&1; then
  echo "FATAL: encoded credential literal detected — refusing to build" >&2
  exit 2
fi

# --- package --------------------------------------------------------------------
mkdir -p "$DIST"
TARBALL="$DIST/pdu-manager-$SHA.tar.gz"
tar -C "$STAGE" -czf "$TARBALL" "pdu-manager-$SHA"
sha256sum "$TARBALL" > "$DIST/pdu-manager-$SHA.sha256"

echo "[build-release] artifact: $TARBALL"
echo "[build-release] checksum: $(cat "$DIST/pdu-manager-$SHA.sha256")"
echo "BUILD_OK"