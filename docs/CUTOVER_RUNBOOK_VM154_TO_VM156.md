# Cutover Runbook — VM154 → VM156 Production Promotion (FW-012/FW-013)

**Status:** READY — awaiting Jordan's explicit approval to execute (Future Work v2 gate; not covered by session auto-merge authorization).
**Date prepared:** 2026-09-27
**Prepared by:** agent (agent_stea004_entrepreneur), based on Future Work v2 §11 + ADR-0004.

## Pre-flight status (all gates verified 2026-09-27)

| # | Gate (FW-011) | Status |
|---|---|---|
| 1 | VM156 runs an approved Git release | ✅ `cba0eec` (main tip, PR #15; deploy transaction ACCEPTED, health 5/5) |
| 2 | Real production PDU backend | ✅ `backend_mode: real` (drop-in + state file; /health verifies) |
| 3 | Read-only real-backend state reads | ✅ 3/3 PDUs, 72/72 outlets read (all ON, matches VM154); zero actuation |
| 4 | FW-001 authoritative labels live | ✅ 153:12 = JetKVM Hardware Console; 153:24 = TESmart 16-Port HDMI KVM Switch; protected [3,4,5,6,9] |
| 5 | Emergency-local web login | ✅ (validate-read-only PASS: 302) |
| 6 | LDAP employee login | ⚠️ TCP+bind+group-search PROVEN from VM156; **employee E2E login needs one real user credential — Jordan to perform at cutover** (2-minute check) |
| 7 | Logging/state writable | ✅ audit.log + jsonl landing; state dir writable |
| 8 | Service survives restart | ✅ (restart → active, health ok) |
| 9 | External monitoring reaches VM156 | ✅ 10.0.20.172 already polling / + /login (~5 min cadence, 12 hits/10min observed) |
| 10 | Auto-rollback proven | ✅ (CI/CD-sprint drill + rollback path rsync-restore verified) |
| 11 | Audit-log rotation installed | ✅ logrotate 3.21 + repo config on VM156 |
| 12 | Secrets outside Git | ✅ provisioned out-of-Git (runbook §2.2); repo secret-scans clean |

## Execution sequence (after Jordan says GO)

```
STEP 1  Record VM154 running release + audit-tail snapshot (evidence file).
STEP 2  Final PBS/backup verification for VM154 + VM156 (cluster backup job covers both; confirm last TASK OK).
STEP 3  VM156 controlled reboot test (FW-011 VM-reboot survival; onboot still 0):
        - qm reboot 156 → wait → verify service active, /health ok, mode=real,
          read-only state read on PDU 153 outlet 12 (GET path only).
STEP 4  Confirm with Jordan (or proxy check) that monitoring shows VM156 healthy.
STEP 5  CUTOVER (order matters, ADR-0004):
        a. qm set 156 --onboot 1                      (VM156 becomes boot-persistent)
        b. Monitoring: confirm external monitor covers VM156 (already does); flag VM154 deprecation to monitor owner.
        c. qm set 154 --onboot 0                      (VM154 will NOT rise at node boot)
        d. qm shutdown 154 --timeout 120              (controlled ACPI shutdown, NOT stop)
        e. Verify: VM156 still healthy; https://10.0.20.156/ serves; monitor green.
STEP 6  Post-cutover records:
        - CURRENT_STATE.md §1 → production = VM156 (https://10.0.20.156/), VM154 = powered-off fallback;
        - AGENTS.md §12 endpoint note updated;
        - handoff + issue #10 comment with evidence.
STEP 7  VM154 stays POWERED OFF, retained as fallback (no deletion this sprint).
```

## Rollback (any failure before/ during STEP 5e)

```
qm start 154                       # VM154 rises with all original state (untouched since capture)
qm set 156 --onboot 0              # optional; VM156 keeps running mock/real per its drop-in
```

VM154 is never modified during the cutover (STEP 1 is read-only), so rollback is a clean `qm start`.

## Hard rules (unchanged)

- ZERO PDU actuation during the entire runbook (validation = GET-only state reads).
- No MIAM-00133 reboot. No VIP/DNS changes (FW-013: 10.0.20.156 is the production address).
- VM154 is shut down, never stopped-forcibly, never deleted.
- If any STEP fails: STOP, record evidence, engage Jordan.

## Open item needing Jordan (2 minutes, at cutover)

- Employee LDAP E2E: log into https://10.0.20.156/ with your normal LLDAP credentials once; confirm role shows (pdu-admin expected). I deliberately did not use your personal credentials.
