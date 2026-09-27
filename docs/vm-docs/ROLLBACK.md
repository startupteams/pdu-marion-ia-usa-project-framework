# ROLLBACK — PDU Manager LLDAP + AI API Upgrade

## Option A — Full VM rollback (nuclear, fastest)

On the Proxmox cluster (via PVE API or GUI, node `miam-00133`):

```bash
qm rollback 154 pre_lldap_api_upgrade_20260910
qm start 154
```

Snapshot taken 2026-09-10 ~22:24 CDT, before any code deploy. Restores app,
config, secrets, service unit, everything. LLDAP changes (groups/users) are NOT
in this snapshot (LLDAP lives on 10.0.20.101) — see Option C for the additive
LLDAP cleanup.

## Option B — Filesystem restore (keeps LLDAP groups)

On VM154 (as root):

```bash
systemctl stop pdu-control
cd /opt/pdu-control
rm -rf app.py app_runtime.py auth_lldap.py action_service.py api_v1.py __pycache__
tar -xzf /root/pdu-control-backups/20260910-222303/pdu-control-app-20260910-222303.tar.gz -C /opt/pdu-control
cp /root/pdu-control-backups/20260910-222303/pdu-control.service.loopback.new /etc/systemd/system/pdu-control.service 2>/dev/null || \
  sed -i 's/--bind 127.0.0.1:5000/--bind 0.0.0.0:5000/' /etc/systemd/system/pdu-control.service
systemctl daemon-reload
chmod 640 /etc/pdu-control/secrets.env && chown root:pducontrol /etc/pdu-control/secrets.env
systemctl start pdu-control
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/   # expect 401 (old Basic auth)
```

Optionally revert TLS/firewall (only if the whole upgrade is being abandoned):

```bash
rm -f /etc/nginx/sites-enabled/pdu-control && systemctl reload nginx
nft flush ruleset && systemctl disable nftables
```

Note: keep the tar backup intact at
`/root/pdu-control-backups/20260910-222303/` (sha256 of tarball:
`d3f9f713252875c3662d593fc7962f74b3a27a0924011cae43820e793e6bc9f4`).

## Option C — LLDAP cleanup (additive changes; remove only if abandoning)

Created on 10.0.20.101 (GraphQL at :17170, admin session):

- Groups (IDs 11-15): `pdu-viewer`, `pdu-operator`, `pdu-admin`, `pdu-ai-agent`,
  `pdu-ai-admin-override`
- Users: `svc-pdu-manager-vm154` (lldap_strict_readonly),
  `miam_0154_pdu_agent` (pdu-ai-agent + pdu-operator; override group was added
  then REMOVED during tests)

Removal via GraphQL `deleteUser` / `deleteGroup` (delete users first, groups
after). Groups alone are harmless if left.

## Post-rollback verification

1. `http://10.0.20.154:5000/` prompts Basic auth (root) — legacy behavior.
2. `systemctl is-active pdu-control` → active.
3. No `/api/v1/*` routes exist after rollback (expected).
4. Protected outlets in UI unchanged: 153 outlets 3,4,5,6,9 blocked for
   OFF/REBOOT, incl. hard 153/9 block in old `validate_item`.

## Data note

The structured audit `audit.log.jsonl` and idempotency store are NEW artifacts;
rollback leaves them in place (harmless). The legacy `audit.log` is append-only
and continuous across the upgrade.
