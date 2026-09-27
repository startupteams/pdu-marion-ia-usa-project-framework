# ADR-0003: CI/CD Deployment Model — systemd-native Releases, Mock-Backend CI, Staging VM156

**Status:** Accepted (human-directed via session authorization, 2026-09-27)
**Date:** 2026-09-27

## Context

The live PDU Manager (captured to this repo via issue #1) is a Python 3.11 / Flask / gunicorn / systemd application on Debian 12, with nginx TLS termination and nftables filtering — no Docker, no database. The CI/CD deployment plan (2026-09-26) requires a reproducible pipeline from Git to staging to production VM154 without ever actuating real PDUs from automation.

## Decision

1. **Runtime model preserved.** The service deploys as Python/systemd (no containerization). The captured stack is systemd-native; introducing Docker now would be a migration the plan forbids without strong reason.
2. **Immutable release layout.** `/opt/pdu-control/releases/<git-sha>/` + `current` symlink; the shared venv at `/opt/pdu-control/venv` is idempotently updated from each release's pinned `requirements.txt`. Mutable site config stays at `/etc/pdu-control/config.json` (outside any release dir, REQ-015).
3. **Artifact-based deploys.** `deploy/build-release.sh` produces `pdu-manager-<sha>.tar.gz` + sha256 sidecar (secret-scan gated). `deploy/deploy-release.sh` performs the transaction: checksum verify → config backup → stage → validate → atomic switch → service restart → health gate → auto-rollback on failure (config + release restore, service restart only).
4. **Mock backend for CI/staging.** `app/mock_pdu_backend.py` implements the driver surface in-memory; `PDU_BACKEND=mock` (worker subprocess + runtime module select) routes ALL reads and actions to the mock — the real SSH driver is never imported. Staging installs with `PDU_STAGING=1 ./deploy/install.sh` and can exercise the complete action path (ON/OFF/REBOOT via API/UI) with zero hardware reachability. Production installs never set the flag.
5. **Staging environment.** VM156 `pdu-manager-staging` on miam-00133 (Debian 12 genericcloud, same OS family as production, 1 vCPU / 1 GiB / 16 GiB, 10.0.20.156) — clean-room acceptance target.
6. **CI.** GitHub Actions on every PR: secret scan, python syntax/compile checks, pytest suite (mock backend, redirected paths, no network), shell syntax checks, artifact build validation. No production credentials in PR jobs (REQ-010).
7. **Runner (deferred).** Self-hosted runner placement is security-sensitive and stays a Proposed-ADR decision for Jordan; this ADR covers repo-side deploy automation only. Until a runner is approved, staging deploys are driven by the agent over SSH with a dedicated key.
8. **Production gate.** Production deploy to VM154 requires the configured human approval (GitHub Environment protection) once a runner exists. Until then, production remains manual-but-scripted (runbook in docs/DEPLOYMENT.md §9).

## Consequences

- Releases are traceable to Git SHAs; ledger at `/var/lib/pdu-control/release-ledger.jsonl`.
- Rollback restores prior release + config backup; tested in staging before any production CD.
- The app gains a tiny backend-selection patch (`PDU_BACKEND` env) — behavior in production unchanged (default = real driver).
- requirements.txt (reconstructed) gets validated on staging on the first clean-room install.

## References

- Requirements: REQ-010, REQ-020, REQ-021, REQ-022, REQ-023
- TDR-0001 (full reproducibility — shrinks when staging clean-room proof lands)
- Capture handoff: docs/handoffs/2026-09-27-source-capture-handoff.md