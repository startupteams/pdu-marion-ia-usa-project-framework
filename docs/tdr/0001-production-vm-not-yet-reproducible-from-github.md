# TDR-0001: Production VM Is Not Yet Reproducible from GitHub

**Status:** In Progress

## Description

The production PDU Manager currently runs on VM154, but the repository has not yet proven that it contains all application source, dependency definitions, service configuration, external configuration, and deployment automation required to recreate the service from a clean VM.

## Impact

- Loss of VM154 could require manual recovery.
- Historical backup copies may be confused with active source.
- Production edits may drift from Git/documentation.
- CI/CD cannot be considered safe until clean deployment and rollback are proven.

## Possible Solutions

- Capture the exact live source using runtime/process evidence.
- Externalize secrets and mutable site configuration.
- Reconstruct/pin dependencies.
- Add deterministic deployment automation.
- Prove a clean staging deployment.
- Add CI, immutable artifacts, approved CD, and rollback.

## Owner

Startup Teams / MARION-IA-USA PDU Manager maintainers
