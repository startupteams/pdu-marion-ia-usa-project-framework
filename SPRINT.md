# Current Sprint

## Sprint Goal

Capture the current live PDU Manager implementation from VM154 into GitHub, prove that the repository can recreate the service in a safe staging environment, and establish the foundation for reviewed CI/CD deployment.

## Features in Scope

### Live Source Capture

**Requirements**

- `REQ-019`
- `REQ-024`
- `REQ-012`

**Work items**

- [ ] Identify the exact live process and source path on VM154 from runtime evidence.
- [ ] Capture application/deployment files without copying historical backups as active source.
- [ ] Produce `docs/CAPTURE_MANIFEST.md`.
- [ ] Externalize/redact secrets from the Git copy.
- [ ] Open a human-reviewed source-capture PR.

### Reproducible Deployment Baseline

**Requirements**

- `REQ-020`
- `REQ-015`
- `REQ-018`

**Work items**

- [ ] Record exact OS/runtime/dependency/service requirements.
- [ ] Create deterministic install/deployment automation.
- [ ] Deploy to a clean staging VM with a non-actuating backend.
- [ ] Compare staging UI/configuration to current VM154.

### CI/CD Foundation

**Requirements**

- `REQ-010`
- `REQ-021`
- `REQ-022`
- `REQ-023`

**Work items**

- [ ] Add safe PR CI.
- [ ] Add immutable release artifact creation.
- [ ] Propose/approve runner and deployment architecture ADR.
- [ ] Add staging deployment.
- [ ] Add protected production deployment and rollback after staging proof.

## Definition of Done

- [ ] The live serving process/source path is unambiguous.
- [ ] No secrets are committed.
- [ ] The source-capture PR is reviewable and traces files to VM154.
- [ ] A clean staging VM can run the service from repository automation.
- [ ] Automated tests never actuate production PDU outlets.
- [ ] Documentation is current.
- [ ] Significant decisions are surfaced through ADRs.
- [ ] Known technical debt is recorded in TDRs.
- [ ] Human reviewer approves before merge.
- [ ] Production deployment is not enabled until staging/rollback validation passes.

## Risks / Blockers / Human Decisions Needed

- Exact current application stack and live source directory must be discovered from VM154.
- Current secret-storage mechanism must be discovered and sanitized before commit.
- Dedicated self-hosted runner placement/permissions require human approval.
- Any runtime-model change (for example introducing Docker where production is not currently Dockerized) requires human-approved architecture decision.

## Sprint Handoff

At completion, summarize live source path, branch/PR, staging reproduction result, CI results, deployment/rollback status, secrets strategy, ADR/TDR state, and unresolved future work.
