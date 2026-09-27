# Current State, Implementation Status, and Known Limitations

**Date:** 2026-09-26

This file intentionally separates what is known from what still needs to be verified from VM154.

## 1. Known current deployment

| Item | Current understanding | Confidence / next step |
|---|---|---|
| Production host | Proxmox `MIAM-00133` | Known |
| Production guest | VM154 | Known |
| Web endpoint | `https://10.0.20.154/` | Known |
| Managed PDU addresses | `10.0.20.151`, `.152`, `.153` | Known environment context; verify config |
| Authentication directory | LLDAP at `10.0.20.101` has been used in this environment | Verify exact VM154 integration |
| Backend style | UI has identified a direct SSH / PowerAlert-style backend | Verify exact code/protocol |
| Source of truth | Live VM is currently the practical source for application implementation | Technical debt being remediated |
| GitHub CI/CD | Not yet established for production | Required target |

## 2. Visible/current feature intent to preserve

These capabilities have been part of the existing PDU Manager design/UI context and must be verified during capture:

- multiple PDU sections;
- outlet number and human-readable labels;
- state display and refresh;
- individual ON/OFF/REBOOT controls;
- selected/batch actions;
- presets;
- protection/lockout behavior;
- recent action feedback/logging;
- configuration save/upload workflow;
- mobile-friendly interface;
- authentication integration.

Do not mark a capability "implemented and verified" in final release documentation until it is confirmed in live code/runtime or safely tested.

## 3. KVM naming state

Current intended naming:

- `MIAM-00172 - JetKVM Hardware Console`
- `MIAM-00182 - TESmart 16-Port HDMI KVM`

Historical backups and scripts may contain older names. Deployment/default config must not restore stale KVM labels.

## 4. Known limitations / risks today

### 4.1 Live VM is not yet reproducible from GitHub

The current production service predates the repository framework. Losing VM154 may require manual reconstruction unless the live source, runtime, configuration model, and dependencies are fully captured.

### 4.2 Live-source location is not yet formally documented

A prior broad text-replacement attempt found many historical `/root` backup/config copies and even modified the executing update script itself. This demonstrates that searching for matching strings is not a reliable way to identify the live application.

The source-capture agent must identify the serving process first and derive the active source/config paths from runtime evidence.

### 4.3 Secrets may be coupled to live configuration

Until the live application is inspected, it is unknown whether PDU/LDAP/session credentials are externalized or embedded in source/config. No capture should be committed until secret scanning and manual review are complete.

### 4.4 Deployment process is not yet source-controlled

There is not yet a validated repository-driven process proving that a clean VM can recreate the application exactly enough to replace production.

### 4.5 CI/CD is not yet implemented

Changes may currently require manual production edits or service-level intervention. The target is a reviewed Git -> staging -> approved production workflow.

### 4.6 Automated real-hardware testing is unsafe

ON/OFF/REBOOT commands cannot be used as routine CI smoke tests. The application needs mocks/non-actuating test paths and read-only production health checks.

### 4.7 Hardware/backend behavior may not be atomic

Network-managed PDUs and SSH/PowerAlert-style interfaces can have latency, communication failures, or ambiguous command outcomes. Application logic must not falsely report success when state cannot be verified.

### 4.8 Runner trust boundary is undecided

A self-hosted deployment runner is likely necessary because production is on a private network. Runner placement and privileges are a security-sensitive architecture decision still requiring explicit approval.

## 5. Not yet implemented / not yet proven

The following are target capabilities and must not be described as complete until verified:

- clean one-command/one-playbook provisioning from a fresh supported OS;
- deterministic dependency pinning derived from the live stack;
- comprehensive automated unit/integration tests;
- mock PDU backend suitable for CI;
- GitHub Actions CI;
- immutable release artifacts;
- automated staging deployment;
- protected production deployment;
- automatic rollback;
- release-SHA visibility in the UI/health endpoint;
- validated restoration of mutable site configuration after rebuild.

## 6. Exit condition for this limitation document

This file should shrink over time. A limitation can be removed only when:

1. the implementation exists;
2. validation is recorded;
3. deployment/operations docs are updated;
4. any related TDR is resolved;
5. a human-reviewed PR has merged.
