# ADR-0005: Self-Hosted Deployment Runner LXC on MIAM-00133

**Status:** Proposed
**Date:** 2026-09-27
**Related:** ADR-0003 (§7 deferred runner), ADR-0004 (VM156 production promotion), Future Work v2 FW-008/FW-009

## Context

Future Work v2 (§9, FW-008) directs: *"Create a small dedicated deployment runner VM or LXC on the Marion management network, preferably on MIAM-00133… It should have only the access required to: receive GitHub Actions jobs, fetch verified release artifacts, connect to VM156, deploy/restart the PDU Manager, perform health checks, trigger rollback. It should not be granted broad administrative access to unrelated infrastructure."*

ADR-0003 §7 explicitly deferred runner placement as security-sensitive. This ADR resolves it.

## Decision (Proposed — awaiting Jordan's approval)

Create **LXC 130 `pdu-deploy-runner`** on MIAM-00133 (Debian 12, 1C/1G, local-lvm):

1. **Scope of access — everything else denied by design:**
   - GitHub: registers as a self-hosted runner for `startupteams/pdu-marion-ia-usa-project-framework` only.
   - VM156: SSH with a **dedicated keypair** (`pdu-runner@vm156`), restricted (in VM156 `authorized_keys`: `from="10.0.20.130"` + forced `sudo -n` allowlist for exactly `pdu-control.service` restart, release-deploy + rollback scripts).
   - GitHub registration token: runner-scoped, rotated at registration; never stored in Git.
   - NO access to VM154, no Proxmox API rights, no LLDAP admin, no PDU credentials — the runner never handles PDU secrets (secrets live on VM156 only; the runner triggers deploys, it does not carry credential payloads).

2. **Job flow (FW-009/FW-010):** `production` environment approval → job dispatched to this runner → runner fetches release artifact + checksum from the workflow run → verifies checksum → runs `deploy-release.sh` over SSH on VM156 → runs `validate-read-only.sh` → reports success/rollback to the workflow.

3. **AI agent role unchanged:** the agent may trigger/observe the workflow after human approval; the runner performs the mechanics.

## Consequences

- Adds one small LXC (~1 GB RAM, ~2 GB disk) to MIAM-00133.
- Self-hosted-runner trust boundary is contained to this repo's deploy jobs + VM156 SSH; even a compromised runner cannot reach PDUs directly (no PDU credentials on it) and cannot actuate (VM156's deploy path is non-actuating by construction).
- If Jordan rejects the LXC placement, fallback = keep the current "agent-over-SSH with protected environment + manual approval" model (already proven in this sprint) — FW-009's gate does not require the runner to exist; it requires the approval gate.

## Alternatives considered

- **GitHub-hosted runner** — cannot reach the private network; requires exposing VM156; rejected.
- **Runner on the workstation** — laptop-dependent, unbounded trust; rejected.
- **Runner on VM156 itself** — deploy machinery on the deploy target; blast-radius coupling; rejected.
- **Existing simpler architecture (agent-driven SSH)** — works and is proven, but bypasses GitHub's audit trail for deployments; FW-009 wants bypass events visible in GitHub. The LXC runner gives auditable deployments; agent-SSH remains the break-glass path.