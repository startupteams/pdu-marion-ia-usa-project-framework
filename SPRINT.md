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

- [ ] Preserve mock backend for CI/staging (verify, don't regress).
- [ ] Real-backend production configuration mechanism for VM156.
- [ ] Provision production secrets to VM156 outside Git.
- [ ] Validate LDAP employee login on VM156.
- [ ] Validate emergency-local root web login on VM156.

### Phase D — GitHub→VM156 Deployment Pipeline (FW-008–010)

**Requirements**

- `REQ-021`, `REQ-022`, `REQ-023`

**Work items**

- [ ] Least-privilege deployment runner (LXC on MIAM-00133).
- [ ] Protected `production` GitHub environment + approval gate.
- [ ] Production deploy workflow (main-only, artifact-verified, non-actuating health checks, auto-rollback).
- [ ] Full pipeline E2E against VM156 in mock mode.
- [ ] Repeat rollback drill.

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

### Phase F — Cutover (FW-012/013) — HUMAN-APPROVAL GATED

**Work items**

- [ ] Human-approved cutover runbook presented with all gates green.
- [ ] Redirect monitoring to VM156.
- [ ] VM156 `onboot=1` + controlled reboot verification.
- [ ] VM154 `onboot=0` + controlled shutdown (retain as fallback).
- [ ] Record cutover in CURRENT_STATE + handoff.

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
