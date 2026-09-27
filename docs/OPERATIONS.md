# PDU Manager Operations Guide

This document explains the operational purpose and safe-use model of the MARION-IA-USA PDU Manager for humans and AI agents.

## 1. Production location

- Proxmox host: `MIAM-00133`
- Guest: **VM156** (production; promoted from staging 2026-09-27)
- Web interface: `https://10.0.20.156/`
- Fallback: VM154 (powered off, untouched; `qm start 154` restores the pre-cutover world)
- Deployment runner: LXC 130 `pdu-deploy-runner` (GitHub Actions; restricted to deploy jobs + VM156)

**Captured facts (2026-09-27):**

| Item | Value |
|---|---|
| Service | `pdu-control.service` (systemd, enabled, Restart=always/5s) |
| App runtime | Flask 3.1.3 + gunicorn 26.1.0 (1 worker, 4 threads) in `/opt/pdu-control/venv`, Python 3.11.2 |
| Bind | 127.0.0.1:5000 (localhost only; nginx terminates TLS :443) |
| App code | `/opt/pdu-control/*.py` (owner root:pducontrol, group-readable) |
| Config | `/etc/pdu-control/config.json` (640 root:pducontrol) |
| Secrets | `/etc/pdu-control/secrets.env` (640 root:pducontrol, 9 B64 vars) |
| Audit log | `/var/log/pdu-control/audit.log` + `audit.log.jsonl` |
| Runtime state | `/var/lib/pdu-control/` (idempotency.json, pending_reboot.json) |
| TLS | `/etc/nginx/ssl/pdu-control.{crt,key}`, self-signed, valid to 2031-09-10 |
| Firewall | nftables `pdu_filter` (22/80/443 from 10.0.20.0/24 + 10.0.10.0/24 only) |
| Logs | journald (`journalctl -u pdu-control`) + nginx `/var/log/nginx/` |

## 2. What operators use the PDU Manager for

Operators use the web application to:

- see configured PDUs and their outlets;
- translate outlet numbers into asset/load names;
- refresh outlet states;
- deliberately request ON/OFF/REBOOT actions;
- perform selected/batch operations;
- use operational presets;
- recognize protected/locked outlets;
- review recent action results/logs;
- save/upload configuration where the current implementation supports it.

## 3. Normal operator workflow

1. Open `https://10.0.20.154/`.
2. Authenticate if prompted.
3. Identify the PDU and outlet by both outlet number and human-readable label.
4. Use **Refresh States** before acting when current state matters.
5. For a power action, verify the intended target again.
6. Check whether the outlet is protected/locked.
7. Execute only the required action.
8. Confirm the resulting status/action log.

A state refresh must be treated as read-only.

## 4. Critical safety rules

- Do not use ON/OFF/REBOOT as a casual connectivity test.
- Do not bypass outlet protection merely because an action is urgent.
- Do not batch-select outlets without reviewing the entire selection.
- Do not test application changes against real actuation when a mock/staging path exists.
- Do not reboot VM154 merely to change labels or normal application code if restarting the application service is sufficient.
- Do not power-cycle `MIAM-00133` through the PDU Manager except under an explicitly approved recovery procedure.

## 5. KVM-related labels

Current intended labels include:

- `MIAM-00172 - JetKVM Hardware Console` (PDU MIAM-00153 / Outlet 12)
- `MIAM-00182 - TESmart 16-Port HDMI KVM Switch` (PDU MIAM-00153 / Outlet 24)

These are the authoritative labels per the 2026-09-17 server-architecture spreadsheet (reconciled into Git 2026-09-27, FW-001). The GitHub repository is the mapping source of truth (FW-002) unless Jordan supplies a newer authoritative mapping. If the UI shows older JetKVM/KYY/HDMI-splitter wording, the production configuration still carries pre-reconciliation labels — update it via the normal Git-managed deployment/config mechanism, not ad-hoc editing. Code deployment must not overwrite current production labels with stale defaults.

## 6. Restart versus VM reboot

A normal PDU Manager application change should normally require only its application service to restart.

**Do not default to rebooting VM154.**

Exact commands (captured from the live VM):

```bash
# Status
systemctl status pdu-control --no-pager

# Restart application only (service-level, no VM reboot)
systemctl restart pdu-control

# Logs
journalctl -u pdu-control -f          # follow
journalctl -u pdu-control -n 200      # recent

# nginx (proxy) — restart only if proxy config changed
systemctl reload nginx

# Verify after restart (read-only)
curl -sk https://10.0.20.154/health
curl -sk -o /dev/null -w '%{http_code}\n' https://10.0.20.154/   # expect 302
```

The service auto-starts on boot (`WantedBy=multi-user.target`, enabled) and self-heals via `Restart=always` / `RestartSec=5`. Startup reconciliation of a pending self-host reboot (153:9) runs automatically in a daemon thread (`reconcile_pending_reboot`).

## 7. Troubleshooting checklist

### Web UI is unavailable

1. Confirm VM154 is running from `MIAM-00133`.
2. Confirm expected listener port is present.
3. Check application/reverse-proxy service status.
4. Review recent logs.
5. Check certificate/proxy errors.
6. Do not reboot the VM until service-level recovery has been attempted and the reason for reboot is understood.

### PDU states show unknown/error

1. Confirm the web application itself is healthy.
2. Check network reachability from VM154 to the affected PDU.
3. Check backend logs for authentication/protocol errors.
4. Do not issue repeated power actions to determine whether communication works.
5. Use a read-only state query if supported.

### Authentication fails

1. Verify application health first.
2. Verify connectivity to the configured directory service.
3. Check application auth logs without printing secrets.
4. Do not weaken authentication to restore convenience.

### Outlet label is wrong

1. Compare against the current server architecture inventory.
2. Determine whether the label comes from mutable configuration, seed config, or code.
3. Update the authoritative source/config path.
4. Ensure CI/deployment cannot restore stale labels later.

## 8. Configuration backup/restore

**Verified live implementation:**

The dashboard exposes **SAVE CONFIG (.md)** / **UPLOAD CONFIG (.md)**. The format is a Markdown batch-selection file, schema version 1:

- `<!-- PDU_BATCH_ACTION=on|off|reboot -->` — the intended action for the selection;
- `<!-- PDU_BATCH_ITEM=<order>|<ip>|<outlet> -->` — one comment per selected outlet;
- human-readable tables (order/pdu/ip/outlet/label) + full PDU inventory for review.

Behavior (verified in `app/app.py`):

- **Export** (`POST /configuration/export`): validates items with action `on` so protected outlets can be saved without implying permission; returns `pdu-master-control-<timestamp>.md` attachment. Contains NO secrets (only asset IDs, IPs, labels).
- **Upload** (`POST /configuration/upload`): accepts `.md/.markdown/.txt` ≤ 1 MB; parses machine-readable comments; validates each target against live config; **rejects OFF/REBOOT selections on protected outlets at upload time**; returns the parsed selection — never executes anything.
- Execution requires an explicit **SEND TO SELECTED** click, which routes through the shared action service (protection re-checked at execution time).

Live site configuration (outlet labels, protection, PDU inventory) itself lives in `/etc/pdu-control/config.json` — backed up outside the app tree. To back it up manually:

```bash
# on VM154 (root)
cp -a /etc/pdu-control/config.json /var/backups/pdu-control-config-$(date +%Y%m%d-%H%M%S).json
```

The authoritative asset/outlet mapping source is Jordan's `Server_Architecture` xlsx (reflected in `/etc/pdu-control/ARCHITECTURE.md`, rev 2026-09-06, also captured at `docs/vm-docs/ARCHITECTURE-notes-2026-09-06.md`).

## 9. Agent operating rule

An AI agent working on this project may perform read-only inspection and software validation, but it must not actuate real outlets unless a human explicitly authorizes the exact production action for the session.

## 10. AI/API access (for agents)

The service exposes `/api/v1` for programmatic use — full guide in `docs/vm-docs/API.md` (captured from the VM). Summary:

- HTTP Basic with LLDAP credentials; TLS cert self-signed (pin sha256 `137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9` or use `curl --cacert`).
- One UUID `Idempotency-Key` per intended action; never retry with a NEW id on timeout (replay semantics: same id replays the original job; new id = new power event).
- Rate limits: 120 GET/min, 20 POST/min per identity (429 + Retry-After).
- `GET /me` → permissions; `GET /pdus` → inventory + busy state; `GET /jobs/{id}` → job status.

The agent test account `miam_0154_pdu_agent` (pdu-ai-agent + pdu-operator) exists for smoke tests; protected-outlet override was exercised once and REVOKED (revocation verified end-to-end).

## 11. Audit log rotation (FW-015)

The repo ships `deploy/logrotate/pdu-control` (weekly, keep 12 archives, compress, copytruncate — conservative until a formal retention period is chosen). Install on a target VM:

```bash
sudo cp /opt/pdu-control/current/deploy/logrotate/pdu-control /etc/logrotate.d/pdu-control
sudo logrotate -d /etc/logrotate.d/pdu-control   # dry-run check
```

Rotation covers both `/var/log/pdu-control/audit.log` and `audit.log.jsonl`. `copytruncate` is used so the running service never needs a reopen signal.
