# IMPLEMENTATION-REPORT — PDU Manager LLDAP + AI API Upgrade

**Date:** 2026-09-10 → 2026-09-11 · **Executor:** agent_stea004_entrepreneur
**Plan:** PDU-MANAGER-LDAP-AI-API-UPGRADE-PLAN-V3 · **Status:** COMPLETE (see caveats)

## Files changed (on VM154)

| File | Change |
|---|---|
| /opt/pdu-control/app.py | REWRITTEN (session login, shared action path, override UI) |
| /opt/pdu-control/app_runtime.py | NEW (shared runtime: locks, audit, worker, reconcile) |
| /opt/pdu-control/auth_lldap.py | NEW (LLDAP bind + groups + central authorization) |
| /opt/pdu-control/action_service.py | NEW (shared action path, invariants, idempotency) |
| /opt/pdu-control/api_v1.py | NEW (/api/v1 blueprint + rate limiter) |
| /opt/pdu-control/pdu_ssh_direct.py | UNTOUCHED (physically-proven driver) |
| /opt/pdu-control/pdu_worker.py | UNTOUCHED |
| /etc/pdu-control/secrets.env | +2 keys (LDAP_SERVICE_USER_B64, LDAP_SERVICE_PASS_B64), perm 640 root:pducontrol |
| /etc/systemd/system/pdu-control.service | --bind 0.0.0.0:5000 → 127.0.0.1:5000 |
| /etc/nginx/sites-available/pdu-control | NEW (TLS :443 → 127.0.0.1:5000; :80 → :443) |
| /etc/nginx/ssl/pdu-control.{key,crt} | NEW self-signed (sha256 DER 137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9) |
| /etc/nftables.conf | NEW (mgmt-only 80/443/22; drops 80/443 otherwise) |
| /var/lib/pdu-control/ | NEW dir (pending_reboot.json, idempotency.json) |
| /var/log/pdu-control/audit.log.jsonl | NEW structured audit |

Package changes: `ldap3==2.9.1` added to /opt/pdu-control/venv (pure Python).
No OS package changes otherwise (nginx/nftables/openssl preinstalled or stock).

## LLDAP (10.0.20.101) — discovered + created

- Protocol port **3890** (plain LDAP; 17170 is web/GraphQL UI only). Base DN
  `dc=example,dc=com`. User DN `uid=<user>,ou=people,dc=example,dc=com`.
- Group search: `(member=<userDN>)` on `ou=groups,...` reading `cn` (LLDAP has
  no memberOf). Bind method: simple. TLS: none (LAN-internal).
- Groups created (IDs): pdu-viewer 11, pdu-operator 12, pdu-admin 13,
  pdu-ai-agent 14, pdu-ai-admin-override 15.
- Users created: `svc-pdu-manager-vm154` (lldap_strict_readonly; service bind
  for group search), `miam_0154_pdu_agent` (pdu-ai-agent + pdu-operator; test
  agent). Passwords staged in 0600 files; never logged or committed.
- Override group was added to the test agent, exercised, then REMOVED —
  revocation verified end-to-end (403 within cache TTL).

## Authorization rules (central, in action_service.check_authorization)

- viewer: pdu-viewer OR pdu-operator OR pdu-admin → read-only.
- normal control (on/off/reboot on non-protected): pdu-operator OR pdu-admin.
- protected override REBOOT: pdu-admin OR (pdu-ai-agent AND pdu-operator AND
  pdu-ai-admin-override), plus admin_override=true + reason + protected ack.
- protected OFF: ALWAYS PROTECTED_OFF_FORBIDDEN for everyone (no bypass path).
- manager config: pdu-admin only. Emergency-local root == pdu-admin (web only).
- 153/9 REBOOT additionally requires acknowledge_controller_may_go_offline.

## Emergency login behavior

- Web: separate emergency form, validates against preserved root credentials
  (secrets WEB_USER/WEB_PASS), audited auth_source=emergency-local, session
  only. Not valid on the API (Basic path is LLDAP-only). Legacy transitional
  Basic-auth on UI routes still accepts emergency root + LLDAP accounts.

## Network access rules

- nginx :443 (TLS) + :80 (redirect) reachable from 10.0.20.0/24 and
  10.0.10.0/24 only (nftables pdu_filter; other sources dropped). Tailscale
  reaches VM154 via routed 10.0.20.x (no tailscale0 iface) — covered.
- gunicorn loopback-only :5000. No WAN exposure. Service starts on boot
  (verified by full VM reboot test).

## Audit

- Legacy human log: /var/log/pdu-control/audit.log (preserved format).
- Structured JSONL: /var/log/pdu-control/audit.log.jsonl (ts, actor, source,
  message; power events include override/reason/request_id/verified state).
- SQLite deferred (documented in HANDOFF.md); JSONL covers §18 fields.

## TLS certificate approach

Self-signed 5-year cert, CN=10.0.20.154, SAN IP:10.0.20.154. Fingerprint
published in API.md for pinning. Agents should use --cacert when possible.

## Test evidence

See TEST-RESULTS.md (dry-run matrix, live auth, live destructive test on
MIAM-00151 outlet 4, protected rejection matrix live, revocation, rate limit,
idempotency incl. cross-restart, reboot test, protection diff MATCH).

## Unresolved issues / deferred

1. Physical protected-outlet reboot not live-fired (no approved safe candidate;
   §24.4 deferral). All rejection paths + the native cycle path verified.
2. SQLite audit DB deferred (JSONL shipped).
3. Dashboard cold-load still serial-reads 3 PDUs (~30-60s) — legacy behavior.
4. Rate limits are starting values; tune after agent onboarding.
5. LDAP is plain (no TLS) on the trusted LAN — matches existing PVE realm
   posture; enable ldaps if LLDAP gains TLS support later.

## Rollback

ROLLBACK.md (VM snapshot, filesystem restore, LLDAP cleanup) — all tested paths
documented; snapshot `pre_lldap_api_upgrade_20260910` verified present.

## Acceptance criteria (plan §25)

All boxes checked except: live protected reboot (deferred per §24.4 — mocked +
rejection-paths proven). Full checklist in HANDOFF.md PROGRESS STATUS table.
