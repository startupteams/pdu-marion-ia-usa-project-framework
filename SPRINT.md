# Current Sprint

## Sprint Goal

Promote VM156 from Git-linked mock staging to the Git-managed production PDU Manager per Future Work v2 (2026-09-27), with VM154 retired to powered-off fallback only after all validation gates pass. (Prior capture + CI/CD sprint completed 2026-09-27: PRs #2–#9, issues #1/#6 closed.)

## Features in Scope

### Phase A — Mapping Reconciliation (FW-001/002)

**Requirements**

- `REQ-003`

**Work items**

- [x] Reconcile 2026-09-17 architecture spreadsheet KVM/PDU labels into Git config, docs, tests.
- [x] Codify Git-as-mapping-authority governance (AGENTS.md §13, ADR-0004).
- [x] Mark legacy pre-V3 bootstrap script as historical reference.

### Phase B/C — VM156 Production Capability (FW-003–007)

**Requirements**

- `REQ-010`, `REQ-015`, plus FW v2 Phase B/C definitions

**Work items**

- [x] Preserve mock backend for CI/staging (verify, don't regress — unchanged and tested).
- [x] Real-backend production configuration mechanism (set-backend-mode.sh; VM156 switched to real 2026-09-27).
- [x] Production secrets provisioned to VM156 outside Git (workstation pipe path; verified).
- [x] LDAP infra validation on VM156 (TCP, service bind, group search) PASS; employee E2E login pending Jordan's one-time check.
- [x] Emergency-local root web login validated (validate-read-only + real logins during authorized tests).

### Phase D — GitHub→VM156 Deployment Pipeline (FW-008–010)

**Requirements**

- `REQ-021`, `REQ-022`, `REQ-023`

**Work items**

- [ ] Least-privilege deployment runner (LXC on MIAM-00133).
- [ ] Protected `production` GitHub environment + approval gate.
- [ ] Production deploy workflow (main-only, artifact-verified, non-actuating health checks, auto-rollback).
- [x] Full pipeline E2E against VM156 (REAL mode via protected environment + LXC 130 runner — run 36341622843 SUCCESS, release 3cd8d89 ACCEPTED).
- [x] Rollback drill proven (CI/CD sprint + rsync-restore verified).
- [x] LXC 130 `pdu-deploy-runner` created + GitHub runner registered (labels: pdu-deploy, vm156-deploy).
- [x] Deploy keypair (ed25519, from-restricted) + sudo allowlist on VM156; repo secret PDU_DEPLOY_SSH_KEY + vars wired.

### Phase E — Production Validation (FW-011)

**Work items**

- [ ] Full VM156 validation: app/HTTPS/health, auth paths, config vs spreadsheet, real-backend read-only state reads, logging/state writability, monitoring reachability.

### Hardening (FW-015–019, non-blocking)

**Work items**

- [ ] Audit-log rotation (logrotate).
- [ ] Remove unused `SNMP_*` secrets from examples/tests.
- [ ] Remove legacy `app/legacy_app_direct.py` after VM156 parity.
- [ ] Config schema validation gate before deployment.
- [ ] Protection-to-asset consistency tests.

### Phase F — Cutover (FW-012/013) — EXECUTED 2026-09-27 (Jordan authorized full session autonomy)

**Work items**

- [x] Runbook merged (PR #16) with 11/12 gates green (employee LDAP E2E still pending Jordan login check).
- [x] VM156 reboot-survival PASS (pre-cutover: qm reboot → health ok, mode=real, 153:12 reads ON).
- [x] Monitoring verified: 10.0.20.172 already polling VM156 (no change needed).
- [x] VM156 `onboot=1`; VM154 `onboot=0` + clean ACPI shutdown (fallback retained).
- [x] Cutover recorded in CURRENT_STATE + handoff.

## Definition of Done

- [ ] All Phase A–E items complete with recorded validation.
- [ ] No PDU actuation occurred at any point (read-only state reads only).
- [ ] CI/staging remains mock-only.
- [ ] Production secrets are outside Git.
- [ ] Cutover executed only after human approval of the runbook.
- [ ] CURRENT_STATE.md identifies VM156 as production post-cutover.
- [ ] ADR/TDR records for significant decisions.
- [ ] Handoff documentation delivered.

## Risks / Blockers / Human Decisions Needed

- Phase F cutover requires explicit human approval (Future Work v2 gate) even under session automation authorization.
- Monitoring redirect to VM156 requires access to the monitor at 10.0.20.172 (coordinate with its owner if not agent-accessible).
- VM154 final disposition (delete/archive/retain) is a later human decision — not this sprint.

## Sprint Handoff

At completion, deliver the handoff markdown (Telegram), covering validation matrix, ADR/TDR state, rollback proof, and exact next actions for the human.
