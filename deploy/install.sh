#!/usr/bin/env bash
# ============================================================================
# deploy/install.sh — clean-VM installer for the PDU Manager (Debian 12)
#
# Usage:   sudo ./deploy/install.sh [path/to/pdu-manager-<sha>.tar.gz]
#          (without an artifact, installs from the current repo tree)
#
# Idempotent. Safe to re-run. Never touches /etc/pdu-control/config.json or
# secrets.env if they already exist (mutable site config preserved, REQ-015).
#
# Staging mode:  PDU_STAGING=1 ./deploy/install.sh ...   → mock PDU backend
#                (sets PDU_BACKEND=mock in the service environment)
# ============================================================================
set -Eeuo pipefail

APP_USER="pducontrol"
OPT_DIR="/opt/pdu-control"
ETC_DIR="/etc/pdu-control"
LOG_DIR="/var/log/pdu-control"
STATE_DIR="/var/lib/pdu-control"
BACKUP_DIR="/var/backups/pdu-control"
STAGING="${PDU_STAGING:-0}"
ARTIFACT="${1:-}"

if [[ $EUID -ne 0 ]]; then
  echo "FATAL: run as root (sudo)." >&2
  exit 1
fi

echo "== [1/9] OS prerequisites =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip openssh-client nginx nftables openssl ca-certificates rsync >/dev/null

echo "== [2/9] Service identity =="
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$OPT_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "== [3/9] Directories =="
mkdir -p "$OPT_DIR" "$LOG_DIR" "$STATE_DIR" "$BACKUP_DIR"
touch "$LOG_DIR/audit.log" "$LOG_DIR/audit.log.jsonl"

echo "== [4/9] Release payload =="
if [[ -n "$ARTIFACT" ]]; then
  SHA_DIR="$(mktemp -d /tmp/pdu-release-XXXXXX)"
  tar -xzf "$ARTIFACT" -C "$SHA_DIR"
  PAYLOAD="$(find "$SHA_DIR" -maxdepth 1 -type d -name 'pdu-manager-*' | head -1)"
  RELEASE_SHA="$(basename "$PAYLOAD" | sed 's/^pdu-manager-//')"
  echo "installing artifact release sha=$RELEASE_SHA"
  RELEASE_DIR="$OPT_DIR/releases/$RELEASE_SHA"
  mkdir -p "$OPT_DIR/releases" "$RELEASE_DIR"
  rsync -a --delete "$PAYLOAD/app/" "$RELEASE_DIR/app/" 2>/dev/null || cp -a "$PAYLOAD/app" "$RELEASE_DIR/app"
  cp -a "$PAYLOAD/requirements.txt" "$RELEASE_DIR/"
  cp -a "$PAYLOAD/deploy" "$RELEASE_DIR/" 2>/dev/null || true
  ln -sfn "$RELEASE_DIR" "$OPT_DIR/current"
  APP_SRC="$RELEASE_DIR/app"
  REQ="$RELEASE_DIR/requirements.txt"
else
  REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  APP_SRC="$REPO_ROOT/app"
  REQ="$REPO_ROOT/requirements.txt"
  ln -sfn "$REPO_ROOT" "$OPT_DIR/current" 2>/dev/null || true
fi

echo "== [5/9] venv + dependencies =="
python3 -m venv "$OPT_DIR/venv"
"$OPT_DIR/venv/bin/pip" install --quiet --upgrade pip
"$OPT_DIR/venv/bin/pip" install --quiet -r "$REQ"

echo "== [6/9] App code into /opt/pdu-control =="
# release dir keeps the immutable copy; the live tree is what the venv+unit use
rsync -a "$APP_SRC/" "$OPT_DIR/" --exclude 'venv' --exclude '__pycache__'
chown -R "$APP_USER:$APP_USER" "$OPT_DIR" "$LOG_DIR" "$STATE_DIR"
chown root:"$APP_USER" "$OPT_DIR" 2>/dev/null || true

echo "== [7/9] Configuration (preserved if present) =="
mkdir -p "$ETC_DIR"
if [[ ! -f "$ETC_DIR/config.json" ]]; then
  SRC_CFG="$(dirname "$0")/../config/examples/config.example.json"
  [[ -n "$ARTIFACT" ]] && SRC_CFG="$(find "$(dirname "$PAYLOAD")" -name 'config.example.json' | head -1)"
  install -o root -g "$APP_USER" -m 640 "$SRC_CFG" "$ETC_DIR/config.json"
  echo "  installed example config -> $ETC_DIR/config.json"
else
  echo "  config.json EXISTS — left untouched"
fi
if [[ ! -f "$ETC_DIR/secrets.env" ]]; then
  echo "  NOTE: $ETC_DIR/secrets.env missing — create it from deploy/examples/secrets.env.example" >&2
  echo "        (staging with PDU_STAGING=1 still needs WEB_PASS for the emergency path)" >&2
fi
if [[ "$STAGING" == "1" && ! -f "$ETC_DIR/secrets.env" ]]; then
  # minimal staging secrets so the app can boot (EXPLICITLY-labeled throwaway
  # test values for the MOCK backend only — never valid for production PDUs)
  _b64() { printf '%s' "$1" | base64 -w0; }
  cat > "$ETC_DIR/secrets.env" <<EOF
# STAGING-ONLY throwaway values (install.sh generated; mock backend active)
PDU_USER_B64=$(_b64 stage-user)
PDU_PASS_B64=$(_b64 stage-pass-not-a-real-secret)
WEB_USER_B64=$(_b64 root)
WEB_PASS_B64=$(_b64 staging-emergency-change-on-first-run)
LDAP_SERVICE_USER_B64=$(_b64 svc)
LDAP_SERVICE_PASS_B64=$(_b64 svc-not-a-real-secret)
EOF
  chown root:"$APP_USER" "$ETC_DIR/secrets.env"; chmod 640 "$ETC_DIR/secrets.env"
  echo "  installed STAGING throwaway secrets (mock backend; rotate on first real use)"
fi

echo "== [8/9] systemd service =="
UNIT_SRC="$(dirname "$0")/systemd/pdu-control.service"
[[ -n "$ARTIFACT" ]] && UNIT_SRC="$(find "$(dirname "$PAYLOAD")" -path '*deploy/systemd/pdu-control.service' | head -1)"
install -m 644 "$UNIT_SRC" /etc/systemd/system/pdu-control.service
if [[ "$STAGING" == "1" ]]; then
  mkdir -p /etc/systemd/system/pdu-control.service.d
  cat > /etc/systemd/system/pdu-control.service.d/mock-backend.conf <<'EOF'
[Service]
Environment=PDU_BACKEND=mock
EOF
  echo "  PDU_BACKEND=mock override installed (staging)"
fi
systemctl daemon-reload
systemctl enable pdu-control >/dev/null

echo "== [9/9] TLS + proxy + firewall =="
if [[ ! -f /etc/nginx/ssl/pdu-control.crt ]]; then
  mkdir -p /etc/nginx/ssl
  openssl req -x509 -newkey rsa:2048 -sha256 -days 1825 -nodes \
    -keyout /etc/nginx/ssl/pdu-control.key -out /etc/nginx/ssl/pdu-control.crt \
    -subj "/CN=$(hostname -I | awk '{print $1}')/O=StartupTeams/OU=MIAM" \
    -addext "subjectAltName=IP:$(hostname -I | awk '{print $1}'),DNS:$(hostname)" 2>/dev/null
  chmod 600 /etc/nginx/ssl/pdu-control.key
  echo "  generated self-signed TLS cert"
fi
NGINX_SRC="$(dirname "$0")/proxy/nginx-pdu-control.conf"
[[ -n "$ARTIFACT" ]] && NGINX_SRC="$(find "$(dirname "$PAYLOAD")" -path '*deploy/proxy/nginx-pdu-control.conf' | head -1)"
install -m 644 "$NGINX_SRC" /etc/nginx/sites-available/pdu-control
ln -sfn /etc/nginx/sites-available/pdu-control /etc/nginx/sites-enabled/pdu-control
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx >/dev/null 2>&1 || true
# reload (not just start) so a pre-existing nginx picks up the new site
systemctl reload nginx 2>/dev/null || systemctl restart nginx

NFT_SRC="$(dirname "$0")/../deploy/nftables.conf"
[[ -n "$ARTIFACT" ]] && NFT_SRC="$(find "$(dirname "$PAYLOAD")" -path '*deploy/nftables.conf' | head -1)"
if nft list ruleset 2>/dev/null | grep -q 'pdu_filter'; then
  echo "  nftables pdu_filter already active — left as-is"
else
  install -m 644 "$NFT_SRC" /etc/nftables.conf
  nft -f /etc/nftables.conf
fi

systemctl restart pdu-control
sleep 2
systemctl is-active pdu-control >/dev/null
curl -fsS http://127.0.0.1:5000/health >/dev/null && echo "HEALTH OK (app)" || { echo "HEALTH FAIL" >&2; exit 3; }
curl -fsSk https://127.0.0.1/health >/dev/null && echo "HEALTH OK (https)" || echo "WARN: https check via nginx failed"

echo "INSTALL_OK"