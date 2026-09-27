# ADR-0002: GitHub Source of Truth with VM154 as the Production Target

**Status:** Accepted  
**Date:** 2026-09-26

## Context

The PDU Manager is already running in production on VM154 hosted by `MIAM-00133`, but the running VM predates a complete source-controlled project/deployment workflow. The system owner has directed that the live PDU Manager code be captured into `startupteams/pdu-marion-ia-usa-project-framework` and that future modifications be delivered through a CI/CD pipeline after validating that the repository can recreate the current service.

A mutable production VM should not remain the only practical source of application code because that creates recovery, review, traceability, and maintainability risk.

## Decision

After live-source capture, secret removal, clean-environment reproduction, and human-reviewed parity validation:

1. the GitHub repository will become the authoritative source for PDU Manager application code and deployment assets;
2. VM154 on `MIAM-00133` will remain the production target unless a later human-approved decision changes it;
3. future application changes will normally flow through branch -> CI -> pull request -> human review -> merge -> staging -> approved production deployment;
4. production code should not normally be edited in place outside this workflow;
5. deployment validation will use non-actuating/read-only checks and will not power-cycle real hardware as an automated test.

This ADR does **not** select the exact packaging technology or self-hosted runner placement. Those decisions depend on the live stack discovered during capture and require separate review if significant.

## Consequences

- Production releases become traceable to Git commits/artifacts.
- Disaster recovery improves because the application can be reconstructed from source plus externalized secrets/configuration.
- Changes gain automated validation and human review.
- A staging/reproducibility environment becomes necessary.
- Secrets and mutable site configuration must be separated from code.
- Some current manual production practices may remain temporarily until the migration is complete.

## References

- Requirements: `REQ-019`, `REQ-020`, `REQ-021`, `REQ-022`, `REQ-023`
- TDR: `../tdr/0001-production-vm-not-yet-reproducible-from-github.md`
