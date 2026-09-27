# TEST-RESULTS — PDU Manager LLDAP + AI API Upgrade (V3)

**Date:** 2026-09-10 → 2026-09-11 (CDT)
**Executor:** agent_stea004_entrepreneur (autonomous, operator asleep)
**Environment:** VM154 `pdu-control` @ 10.0.20.154, node miam-00133

Legend: ✅ pass · ⚠️ pass with noted caveat · ❌ fail (none outstanding)

---

## 1. Local dry-run enforcement tests (mocked driver, §24.2)

| # | Case | Result |
|---|---|---|
| 1.1 | protected OFF — viewer | ✅ 403 PROTECTED_OFF_FORBIDDEN |
| 1.2 | protected OFF — operator | ✅ 403 PROTECTED_OFF_FORBIDDEN |
| 1.3 | protected OFF — pdu-admin + admin_override | ✅ 403 PROTECTED_OFF_FORBIDDEN |
| 1.4 | protected OFF — AI agent + override group + admin_override + acks | ✅ 403 PROTECTED_OFF_FORBIDDEN |
| 1.5 | protected OFF — emergency-local root + admin_override | ✅ 403 PROTECTED_OFF_FORBIDDEN |
| 1.6 | protected REBOOT — viewer/operator/ai-agent(no override grp) | ✅ 403 NOT_AUTHORIZED |
| 1.7 | protected REBOOT — override group but admin_override=false | ✅ 403 NOT_AUTHORIZED |
| 1.8 | protected REBOOT — override group, no reason | ✅ 403 OVERRIDE_REASON_REQUIRED |
| 1.9 | protected REBOOT — override group, no protected ack | ✅ 403 PROTECTED_ACK_REQUIRED |
| 1.10 | protected REBOOT — pdu-admin, all fields | ✅ authorized (driver mocked) |
| 1.11 | protected REBOOT — AI with override grp, all fields | ✅ authorized (driver mocked) |
| 1.12 | 153/9 reboot without self-host ack | ✅ 403 SELF_HOST_ACK_REQUIRED |
| 1.13 | normal ON — viewer | ✅ 403 NOT_AUTHORIZED |
| 1.14 | normal ON — operator | ✅ authorized |
| 1.15 | normal ON — no PDU group | ✅ 403 NOT_AUTHORIZED |
| 1.16 | batch containing protected OFF target | ✅ whole batch rejected, nothing dispatched |

## 2. Live authentication tests (no power changes, §24.1)

| # | Case | Result |
|---|---|---|
| 2.1 | GET /api/v1/me, valid LLDAP agent | ✅ 200, groups [pdu-ai-agent, pdu-operator] |
| 2.2 | GET /me, wrong password | ✅ 401 |
| 2.3 | GET /me, no auth | ✅ 401 + WWW-Authenticate |
| 2.4 | GET /me, local root Basic on API | ✅ 401 (no local-root on API, per §21) |
| 2.5 | UI login valid LLDAP | ✅ 302 → dashboard 200, session cookie |
| 2.6 | UI login emergency root | ✅ 302 → dashboard 200, EMERGENCY LOCAL chip |
| 2.7 | UI logout | ✅ 302 → login |
| 2.8 | dashboard role gating | ✅ agent sees 0 override buttons; root sees 5 |
| 2.9 | audit of logins | ✅ login=SUCCESS lines with auth_source + groups |

## 3. Live read-only API (§13)

| # | Case | Result |
|---|---|---|
| 3.1 | GET /health | ✅ 200 (no auth required) |
| 3.2 | GET /pdus | ✅ 3 PDUs, protected_outlets [3,4,5,6,9] on 153 |
| 3.3 | GET /pdus/MIAM-00151/outlets/4 | ✅ state ON, protected false |
| 3.4 | GET /pdus/MIAM-00153/outlets/9 | ✅ protected true + self_host_dependency true |
| 3.5 | external HTTPS access | ✅ 200 from 10.0.20.x host; http→301; TLS live |

## 4. Live destructive-path test — MIAM-00151 outlet 4 (§24.3)

Pre-checks: outlet unlabeled ("Outlet 4"), not protected, no mapped asset (config + UI). ✅

| # | Step | Result |
|---|---|---|
| 4.1 | initial state | ✅ ON |
| 4.2 | POST off (unique Idempotency-Key) | ✅ 202, job 303c1d8a6c5f |
| 4.3 | replay same key | ✅ same job_id, replayed=true, no second power event |
| 4.4 | reuse key w/ different action | ✅ 409 DUPLICATE_REQUEST_CONFLICT |
| 4.5 | post-OFF state verify | ✅ OFF |
| 4.6 | POST on (restore) | ✅ 202, job succeeded |
| 4.7 | post-ON state verify | ✅ ON (outlet left ON) |
| 4.8 | cross-restart idempotent replay | ✅ same job returned after service restart |
| 4.9 | rate limit | ✅ 19-20 POSTs pass, then 429 + Retry-After (live) |
| 4.10 | audit trail | ✅ ACCEPTED→SENT→SUCCESS w/ actor, request_id, override, reason |

## 5. Protected-outlet rejection tests — LIVE, no command transmitted (§24.2)

Target MIAM-00153 outlet 9 (protected + self-host). Agent had NO override group at first:

| # | Request | Result |
|---|---|---|
| 5.1 | OFF w/ admin_override + acks | ✅ 403 PROTECTED_OFF_FORBIDDEN (no SSH to PDU logged) |
| 5.2 | REBOOT w/o override group, all acks | ✅ 403 NOT_AUTHORIZED |
| 5.3 | (override group added) REBOOT w/o self-host ack | ✅ 403 SELF_HOST_ACK_REQUIRED |
| 5.4 | (override group removed) REBOOT with ALL acks | ✅ 403 NOT_AUTHORIZED — **revocability proven end-to-end** (§28) |

## 6. Native-cycle verification (§14.2)

- Driver inspection: `reboot` → menu `4- Cycle Load` on the PDU's own Load Detail
  menu, confirmed via PowerAlert "are you sure" y+ENTER flow; driver closes the
  session and verifies with a FRESH session — the cycle is committed by the PDU
  itself. ✅ atomic-native, not controller OFF→ON.
- Self-host flow writes `pending_reboot.json`, commits the cycle, returns;
  boot-time reconciler reads state, recovers ON if needed, audits, clears. ✅ coded;
  NOT live-fired (see §7 caveat).

## 7. Deferred / caveats

- ⚠️ **Physical protected-outlet reboot not fired live** (§24.4 allows deferral when no
  safe candidate is approved; all rejection paths proven live; the cycle path is the
  same production-proven driver operation used for legacy reboots — audit rows from
  2026-09-10/11 show REBOOT verified_state=SUCCESS on 151/3 and 152/3).
- ⚠️ Idempotency persistence added mid-test; the 4.4 conflict window spans restarts
  only from the moment the persistence file exists (deployed and verified).
- ⚠️ Dashboard cold-load does 3 serial SSH state reads (~30-60s) — legacy behavior
  preserved; API read path unaffected (per-PDU reads, cached).

## 8. Infrastructure tests

| # | Case | Result |
|---|---|---|
| 8.1 | nginx TLS :443 + :80→301 | ✅ |
| 8.2 | gunicorn 127.0.0.1:5000 only | ✅ ss verified |
| 8.3 | nftables mgmt-only (20/24 + 10/24) | ✅ rules loaded + enabled |
| 8.4 | VM154 full reboot → services auto-start | ✅ pdu-control, nginx, nftables active; HTTPS health 200 on first probe after boot |
| 8.5 | protection flags before vs after | ✅ EXACT MATCH {153:[3,4,5,6,9]}, snapshots written |
| 8.6 | PVE snapshot rollback point | ✅ `pre_lldap_api_upgrade_20260910` (task OK) |
