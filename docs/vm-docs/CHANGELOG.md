# CHANGELOG — PDU Manager (VM154) Upgrade 2026-09-10/11

Implements PDU-MANAGER-LDAP-AI-API-UPGRADE-PLAN-V3.

## Added

- **LLDAP authentication everywhere**: browser session login (LLDAP), HTTP
  Basic LLDAP auth for `/api/v1`, service bind account for group lookup.
  Emergency local `root` web login preserved (session path only).
- **REST API v1** (`/api/v1`): health, me, pdus, outlet detail, single +
  batch actions, job status, audit tail. JSON only. OpenAPI spec + agent guide.
- **Central Action Service**: one shared path for UI + API (authz → protection
  invariant → idempotency → audit pre-record → per-PDU lock → driver → verify
  → audit completion).
- **Hard protected-outlet state machine (§29)**: protected OFF forbidden for
  every role/flag (admins, override agents, emergency root, batches, presets);
  protected REBOOT only via admin override + reason + ack; expected final ON;
  recovery-on with limited retries; high-severity audit on failure.
- **Controller self-dependency (153/9)**: `self_host_dependency` metadata,
  extra `acknowledge_controller_may_go_offline`, native PDU cycle commit +
  boot-time reconciliation (`/var/lib/pdu-control/pending_reboot.json`).
- **Idempotency**: `Idempotency-Key` header or `request_id` body; replay
  returns original job; conflict → 409; 24h retention; persists across restarts.
- **Rate limiting**: 120 GET/min, 20 POST/min per identity → 429 + Retry-After.
- **waiting_for_pdu_lock**: API submissions queue behind an active PDU op.
- **Structured audit** (`audit.log.jsonl`) alongside legacy text audit log.
- **TLS reverse proxy** (nginx :443, self-signed; :80 → :443) and **nftables**
  mgmt-only exposure (10.0.20.0/24, 10.0.10.0/24; 80/443 dropped elsewhere).
- **UI**: login page (LLDAP + emergency), user/role banner, logout, override
  button for authorized users (protected outlets, reason required, self-host
  warning for 153/9), API link.
- **LLDAP groups**: pdu-viewer, pdu-operator, pdu-admin, pdu-ai-agent,
  pdu-ai-admin-override (independently revocable override).
- **Test agent account**: `miam_0154_pdu_agent` (pdu-ai-agent + pdu-operator).

## Changed

- gunicorn binds `127.0.0.1:5000` (was `0.0.0.0:5000`); nginx terminates TLS.
- `/api/action` + `/api/batch` (UI routes) now route through the shared Action
  Service with actor context (was: direct reserve_and_start).
- `validate_item` → `action_service.validate_target` (protection + self-host
  metadata added; old hard 153/9 OFF+REBOOT block replaced by §14 policy).
- secrets.env gained `LDAP_SERVICE_USER_B64` / `LDAP_SERVICE_PASS_B64`
  (perm 640 root:pducontrol preserved).

## Preserved (unchanged)

- PDU driver `pdu_ssh_direct.py` — untouched, physically-proven.
- Legacy UI layout, presets, Markdown export/upload, drag-select, mobile
  behavior, per-PDU busy indicators, action-log download.
- Protection flags: 153 [3,4,5,6,9] — verified identical pre/post (snapshots).
- Emergency root Basic-auth fallback for legacy scripts (transitional gate).

## Security notes

- No PDU SSH credentials exposed to agents; agents only ever talk to the API.
- No arbitrary command endpoint; only explicit PDU operations.
- TLS required for production (Basic creds); self-signed cert
  sha256 137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9.
- Local root never valid on the API; web-only emergency path, audited.
