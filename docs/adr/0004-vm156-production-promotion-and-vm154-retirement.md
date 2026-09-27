# ADR-0004: VM156 Promotion to Git-Managed Production; VM154 Retirement to Cold Fallback

**Status:** Accepted (human-directed; Jordan's Future Work v2 §2 decision table, 2026-09-27)
**Date:** 2026-09-27
**Related:** ADR-0002 (GitHub source of truth / VM154 production), ADR-0003 (CI/CD deployment model), Future Work v2 (2026-09-27)

## Context

ADR-0002 fixed VM154 as the production target at capture time. Since then the repository gained: a captured source baseline (PRs #2–#5), a CI/CD pipeline with immutable artifacts, transactional deploys, and proven auto-rollback on staging VM156 in mock mode (PRs #7–#9). Jordan has now resolved the open deployment decisions in Future Work v2:

- VM156 is to be **promoted from mock staging to Git-managed production**.
- VM154 is **retired to powered-off fallback** (`onboot=0`, shut down, retained temporarily — not deleted).
- Deployment flows GitHub → approval gate → VM156 automatically after human approval.
- After the 2026-09-17 architecture spreadsheet is reconciled into Git (FW-001), **GitHub is the authoritative hardware mapping source**, superseding the VM's ad-hoc files.
- Human approval gates production deployment by default; an authorized admin override is permitted and must be auditable; an AI agent may execute the deployment only after approval/override.
- LDAP employee login and emergency-local root web login are both permanent features.
- Mock backend remains a permanent CI/staging capability and must never be enabled in production.

## Decision

1. **VM156 on `MIAM-00133` becomes the production PDU Manager host** after passing the FW-011 production-capable validation (application, authentication, configuration, real-backend read-only validation, logging/state, monitoring).
2. **VM154 is retired**: `onboot=0`, controlled shutdown, retained powered-off as fallback for an observation period. It is not deleted during this sprint; final disposition is a later human decision (Future Work v2 §17).
3. **Production endpoint during initial cutover is `https://10.0.20.156/`** — no IP reassignment or VIP is introduced for cosmetic continuity (Future Work v2 §11/FW-013).
4. **Hardware/outlet mapping authority transfers to Git** after the FW-001 reconciliation merges (governance codified in AGENTS.md §13).
5. **Production deployment to VM156 requires the protected-environment approval gate**; the AI agent may run the deployment mechanics only after human approval or an audited authorized override.
6. **Non-actuating validation is a permanent invariant**: production deploy verification must never send ON/OFF/REBOOT/Cycle Load to a physical outlet (Future Work v2 §14.3).

## Consequences

- `/etc/pdu-control` (config + secrets) and `/var/lib/pdu-control` (state) on VM156 must be provisioned as production data before cutover; releases may only replace application code.
- Monitoring must be redirected from VM154 to VM156 as part of cutover; no stale monitor may keep treating VM154 as the sole production endpoint.
- VM156 receives `onboot=1` only at cutover and must be verified across a controlled reboot; it stays `onboot=0` while staging.
- VM154's `onboot` must be set to 0 BEFORE shutdown so an unplanned node reboot cannot resurrect two competing production instances.
- The mock backend stays available for CI/staging; production secrets (real PDU credentials, LDAP bind, session secret, emergency-local credential) live only on VM156 in `/etc/pdu-control/secrets.env`, never in Git.
- Rollback from a failed VM156 production release returns the previous release's code (rsync-based), preserving config/state — proven in the staging rollback drill.
- VM154 remains available for fallback until VM156 has operated successfully for an agreed observation period; a future cleanup task may archive or delete it.

## Failure modes considered

- **Two live production instances after node reboot** — mitigated by the `onboot` flip sequence (156→1 before 154→0) and by shutting VM154 down only after VM156 is verified on the real backend.
- **Split-brain actuation paths** — only one service instance holds production PDU credentials; VM154's copy is untouched during VM156 promotion and VM154 is shut down, not repurposed.
- **Stale monitoring targeting a dead host** — cutover includes explicit monitoring redirection and verification.
- **Unapproved production deploys** — protected GitHub environment approval gate; bypass events are auditable by design (environment protection logs).
