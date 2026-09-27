# PDU Manager API v1 — Agent Guide

**Base URL:** `https://10.0.20.154/api/v1` (TLS, self-signed cert — pin the fingerprint below)
**Auth:** HTTP Basic with your **LLDAP username/password**. No API keys, no tokens.
**All responses:** JSON.

## TLS fingerprint (sha256, DER)

```
137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9
```

Prefer verifying: `curl --cacert pdu-control.crt ...` if your environment has the cert;
otherwise use `-k` on trusted management networks only.

## Safe credential handling

```bash
export PDU_API_BASE='https://10.0.20.154/api/v1'
export PDU_LDAP_USER='miam_0154_pdu_agent'
read -rsp 'LLDAP password: ' PDU_LDAP_PASSWORD; export PDU_LDAP_PASSWORD
AUTH=(-u "$PDU_LDAP_USER:$PDU_LDAP_PASSWORD")
```

## The Safe AI Workflow (follow exactly)

1. Authenticate: `GET /me` — confirm `can_control`.
2. `GET /pdus/{pdu_id}/outlets/{n}` — confirm outlet name and current state.
3. Check `protected` and `self_host_dependency` metadata.
4. Generate ONE unique `request_id` (UUID) for the intended action.
5. POST exactly one action with that `Idempotency-Key`.
6. Save the returned `job_id`.
7. Poll `GET /jobs/{job_id}` — the SAME id, never a new submit.
8. Verify final outlet state.
9. **Do NOT retry with a new request id because your client timed out.** The
   idempotency layer replays safely; a new id creates a NEW power event.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | /health | none | liveness |
| GET | /me | Basic | identity + permissions |
| GET | /pdus | Basic | inventory + busy state |
| GET | /pdus/{pdu_id}/outlets/{n} | Basic | outlet detail (state, protected, self_host) |
| POST | /pdus/{pdu_id}/outlets/{n}/actions | Basic | submit on/off/reboot (202 + job_id) |
| POST | /actions/batch | Basic | explicit target list (202 + job ids) |
| GET | /jobs/{job_id} | Basic | job status/history |
| GET | /audit?limit=N | Basic | structured audit tail |

`pdu_id` accepts the asset id (`MIAM-00151`) or the IP (`10.0.20.151`).

## Examples

```bash
# who am I
curl "${AUTH[@]}" "$PDU_API_BASE/me"

# read an outlet
curl "${AUTH[@]}" "$PDU_API_BASE/pdus/MIAM-00151/outlets/4"

# turn a NORMAL outlet ON (idempotent)
RID="$(uuidgen)"
curl "${AUTH[@]}" -H 'Content-Type: application/json' -H "Idempotency-Key: $RID" \
  -X POST -d '{"action":"on","reason":"Approved automated recovery"}' \
  "$PDU_API_BASE/pdus/MIAM-00151/outlets/4/actions"
# -> 202 {"accepted":true,"job_id":"...","status":"queued",...}

# poll the job
curl "${AUTH[@]}" "$PDU_API_BASE/jobs/<job_id>"
```

## Actions & authorization

- `on` / `off` / `reboot` on **normal** outlets: requires `pdu-operator` or `pdu-admin`.
- **Protected outlets** (`protected: true` — currently MIAM-00153 outlets 3,4,5,6,9):
  - `on` — allowed for operators.
  - `off` — **ALWAYS REJECTED** (`PROTECTED_OFF_FORBIDDEN`) for everyone, including
    admins and override-capable agents. Protected outlets must remain ON.
  - `reboot` — only with ALL of:
    - membership in `pdu-admin` (humans) or `pdu-ai-agent` + `pdu-operator` + `pdu-ai-admin-override` (agents),
    - `"admin_override": true`,
    - non-empty `"reason"`,
    - `"acknowledge_protected_device": true`,
    - and for MIAM-00153 **outlet 9** (powers MIAM-00133, the host running this controller):
      `"acknowledge_controller_may_go_offline": true`.
  - The reboot uses the PDU's native atomic Cycle-Load; expected final state is ON.
  - Override permission is **revocable**: remove `pdu-ai-admin-override` from the agent's
    LLDAP account and its next override attempt is rejected within ~60s (group cache TTL).

## Rate limits (per identity)

- GET: 120/minute → `429` + `Retry-After`
- POST (power actions): 20/minute → `429` + `Retry-After`
- Idempotent replays do NOT consume a new power event.
- One active operation per PDU; concurrent API submits queue as `waiting_for_pdu_lock`.

## Idempotency

Send either header `Idempotency-Key: <uuid>` or body field `"request_id": "<uuid>"`.
Replays of the same key return the ORIGINAL job (`"replayed": true`). Reusing a key
for a different action/target set returns `409 DUPLICATE_REQUEST_CONFLICT`.
Retention: 24h (persists across service restarts).

## Error codes

`AUTH_REQUIRED` `AUTH_INVALID` `AUTH_LDAP_UNAVAILABLE` `NOT_AUTHORIZED`
`UNKNOWN_PDU` `UNKNOWN_OUTLET` `INVALID_ACTION` `INVALID_REQUEST`
`PROTECTED_OUTLET` `PROTECTED_OFF_FORBIDDEN` `OVERRIDE_REASON_REQUIRED`
`PROTECTED_ACK_REQUIRED` `SELF_HOST_ACK_REQUIRED` `PDU_BUSY`
`DUPLICATE_REQUEST_CONFLICT` `RATE_LIMITED` `PDU_SSH_FAILURE` `UNKNOWN_JOB`

HTTP mapping: 200 read · 202 accepted · 400 invalid · 401 auth · 403 policy ·
404 unknown · 409 busy/conflict · 429 rate · 500 app · 502 PDU comm failure · 503 LDAP down.

## Batch example (no protected targets)

```bash
curl "${AUTH[@]}" -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" \
  -X POST -d '{
    "action": "on",
    "targets": [{"pdu_id":"MIAM-00151","outlet":13},{"pdu_id":"MIAM-00152","outlet":13}],
    "reason": "Power on approved GPU systems"
  }' "$PDU_API_BASE/actions/batch"
```

A batch containing any protected target with `off` is rejected whole
(all-or-nothing validation before any command). Protected `reboot` targets pass
the same per-target override checks; 153/9 additionally requires the
controller-offline acknowledgement.
