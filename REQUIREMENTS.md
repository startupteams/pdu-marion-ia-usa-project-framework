# Requirements

This file is the source of truth for **approved PDU Manager product scope**.

Requirements are intentionally implementation-neutral unless a deployment/safety constraint is itself part of the approved scope. Acceptance criteria live directly with each requirement.

## Business goals

The PDU Manager shall reduce operational friction and risk when managing rack power in MARION-IA-USA by providing a centralized, human-readable, auditable, and recoverable interface to network-managed PDUs.

Key business outcomes are:

- fewer trips to physically access rack equipment;
- lower risk of powering off the wrong device;
- faster troubleshooting and recovery;
- consistent asset/outlet naming aligned with server architecture records;
- traceable operational actions;
- recoverability if VM154 is lost;
- safe, reviewable changes through Git and CI/CD rather than ad-hoc production edits.

---

## Feature: PDU Inventory and Human-Readable Mapping

### REQ-001 - Present configured PDUs in one interface

**Requirement**

The product shall present the configured network-managed PDUs through one browser-accessible interface.

**Acceptance criteria**

- The operator can distinguish each configured PDU.
- Each PDU displays its configured identity and address/role without exposing credentials.
- Failure of one PDU does not make the identity of the other configured PDUs ambiguous.

**Verification**

- UI/integration test with mock PDU inventory.
- Read-only production comparison after deployment.

### REQ-002 - Display human-readable outlet identity

**Requirement**

The product shall display each outlet with a stable outlet number and a human-readable asset/load label.

**Acceptance criteria**

- Outlet number remains visible even when a custom label exists.
- Empty/unassigned outlets are distinguishable from named assets.
- Displayed labels are loaded from controlled configuration rather than hard-coded in presentation logic where practical.

**Verification**

- Unit/configuration test.
- UI comparison against approved architecture mapping.

### REQ-003 - Preserve approved KVM naming

**Requirement**

The product shall use the current approved KVM-related asset names in production configuration.

**Acceptance criteria**

- The JetKVM asset is labeled `MIAM-00172 - JetKVM Hardware Console` where applicable.
- The TESmart KVM asset is labeled `MIAM-00182 - TESmart 16-Port HDMI KVM Switch` where applicable.
- Deprecated KVM naming is not reintroduced by deployment defaults or seed configuration.

**Resolution (2026-09-27, FW-001):** the 2026-09-17 server-architecture spreadsheet is the authoritative mapping source (per Future Work v2 §2–§3). Outlet 153:12 = `MIAM-00172 - JetKVM Hardware Console`; outlet 153:24 = `MIAM-00182 - TESmart 16-Port HDMI KVM Switch`. The historical "live = truth" rule applied during capture is superseded for labels by this reconciliation; Git is the mapping source of truth going forward (FW-002).

**Verification**

- Configuration test and read-only UI inspection.

---

## Feature: Outlet State and Power Control

### REQ-004 - Display outlet power state

**Requirement**

The product shall display the latest available power state for each managed outlet.

**Acceptance criteria**

- The UI differentiates at least ON, OFF, and unknown/error state.
- Refreshing state does not actuate an outlet.
- Backend failure is surfaced rather than silently represented as a successful state.

**Verification**

- Mock-backend integration tests.
- Read-only state retrieval test where safely supported.

### REQ-005 - Support deliberate individual outlet actions

**Requirement**

The product shall support deliberate operator-requested ON, OFF, and REBOOT/CYCLE actions for outlets that are not blocked by safety policy.

**Acceptance criteria**

- The requested outlet and requested action are unambiguous before execution.
- The application reports success/failure returned by the backend.
- A page refresh or state query never implicitly executes a power action.

**Verification**

- Automated tests use a mock PDU backend only.
- Real hardware actuation is manual and separately authorized, not part of CI.

### REQ-006 - Support selected/batch power actions

**Requirement**

The product shall allow an operator to select multiple permitted outlets and intentionally issue one supported action to that selection.

**Acceptance criteria**

- The current selection is visible before action execution.
- The operator can clear/select the selection without changing power state.
- Per-outlet results are visible when a batch action contains mixed outcomes.
- Protected outlets remain protected during batch actions.

**Verification**

- Mock-backend integration test.

### REQ-007 - Support operational presets

**Requirement**

The product shall support named presets/groupings for recurring operational selections/actions as configured by authorized operators.

**Acceptance criteria**

- Preset membership is inspectable.
- Applying/selecting a preset does not itself perform an unconfirmed dangerous power action unless explicitly designed and approved to do so.
- Protected outlets cannot be bypassed merely by being included in a preset.

**Verification**

- Unit/integration tests against mock configuration/backend.

---

## Feature: Safety and Protection

### REQ-008 - Protect critical outlets

**Requirement**

The product shall support protection/lockout rules that prevent dangerous actions against designated critical outlets unless an explicitly authorized override path is used.

**Acceptance criteria**

- Protected outlets are visibly distinguishable.
- Normal OFF and REBOOT/CYCLE paths cannot bypass protection.
- Batch actions respect protection.
- The production mapping protects critical infrastructure according to current operator policy, including the protection intent for `MIAM-00133` where its power path is represented.

**Verification**

- Automated policy tests using mocks/config fixtures.
- Read-only inspection of production protection configuration.

### REQ-009 - Fail safe when backend state is uncertain

**Requirement**

The product shall not represent an unverified power action as successful when communication with a PDU fails or returns an ambiguous result.

**Acceptance criteria**

- Communication errors are visible to the operator.
- Ambiguous results are not converted to success.
- The UI permits a subsequent read-only refresh/reconciliation.

**Verification**

- Fault-injection tests against the mock backend.

### REQ-010 - Prevent automated production actuation during CI/deployment validation

**Requirement**

The project shall provide a validation mode/path in which automated CI, staging, and production health checks cannot send real outlet actuation commands.

**Acceptance criteria**

- PR CI requires no production PDU actuation credentials.
- Staging defaults to a mock/non-actuating backend.
- Production deployment health checks use only application/read-only checks.
- No deployment workflow issues ON/OFF/REBOOT/CYCLE as a smoke test.

**Verification**

- CI workflow review and tests.
- Deployment-script static review.

---

## Feature: Authentication and Access Control

### REQ-011 - Authenticate operator access

**Requirement**

The product shall require the configured authentication mechanism for operator access to protected PDU Manager functions.

**Acceptance criteria**

- Unauthenticated users cannot execute power-control actions.
- Authentication failure does not expose secrets.
- Current LLDAP integration at `10.0.20.101`, if present in the live service, is documented and reproducible without committing bind credentials.

**Verification**

- Authentication integration tests where safe.
- Source/runtime inspection during capture.

### REQ-012 - Keep credentials outside source control

**Requirement**

The product and deployment shall keep PDU, LDAP, session, TLS-private-key, and deployment credentials outside Git.

**Acceptance criteria**

- Repository secret scanning passes.
- Example configuration contains placeholders only.
- Deployment documentation specifies secret names/locations without values.
- Runtime secrets are readable only by the required service/deployment identities.

**Verification**

- Secret scan and permissions review.

---

## Feature: Logging, Auditability, and Diagnostics

### REQ-013 - Record operator power actions

**Requirement**

The product shall record enough information about power actions to support troubleshooting and accountability.

**Acceptance criteria**

- Log entry includes timestamp, target outlet/device, requested action, and outcome.
- Authentication identity is recorded where available and appropriate.
- Logs do not expose passwords, private keys, or session secrets.

**Verification**

- Mock-action log test.
- Log redaction review.

### REQ-014 - Provide operator-visible recent-action feedback

**Requirement**

The product shall provide recent-action/result feedback in the web interface or an equivalently accessible operational view.

**Acceptance criteria**

- Recent success/failure outcomes are visible without shell access.
- A failed backend operation is distinguishable from a successful one.

**Verification**

- UI/integration test.

---

## Feature: Configuration Management

### REQ-015 - Preserve configuration across application deployments

**Requirement**

Deploying new application code shall not silently overwrite the production outlet mapping, presets, protection settings, or other mutable site configuration.

**Acceptance criteria**

- Mutable site configuration is stored outside immutable code release directories or otherwise protected from overwrite.
- Deployment creates a recoverable backup before any compatible configuration migration.
- Rollback does not unexpectedly restore stale site mappings.

**Verification**

- Staging upgrade/rollback test.

### REQ-016 - Support configuration export/import behavior that is documented and validated

**Requirement**

If the production product exposes configuration save/upload functionality, the repository shall document its format, validation rules, and recovery behavior.

**Acceptance criteria**

- Export does not include secrets unless explicitly designed, encrypted, and approved.
- Import validates structure before applying changes.
- Invalid configuration does not leave the application in a partially applied state.
- The exact currently implemented `.md` configuration workflow shown by the production UI is verified during source capture before being described as fully implemented.

**Verification**

- Source inspection and staging tests.

---

## Feature: Web User Experience

### REQ-017 - Provide browser-based management UI

**Requirement**

The product shall provide a browser-based interface suitable for routine desktop operation and practical mobile use.

**Acceptance criteria**

- Primary PDU/outlet controls are usable without direct shell access.
- Core views do not require horizontal layouts that make normal mobile operation impractical.
- Error and protection states are visible.

**Verification**

- UI review at representative desktop/mobile widths.

### REQ-018 - Expose production service over HTTPS

**Requirement**

The production web interface shall be served over HTTPS on the management network.

**Acceptance criteria**

- `https://10.0.20.154/` or its approved successor responds over HTTPS.
- TLS private keys are not stored in Git.
- Certificate strategy is documented in deployment documentation.

**Verification**

- HTTPS health check and configuration review.

---

## Feature: Reproducibility and Source Control

### REQ-019 - GitHub shall become the authoritative application source

**Requirement**

After parity validation and human approval, the GitHub repository shall be the authoritative source for PDU Manager application code and deployment assets.

**Acceptance criteria**

- Live source has been captured from VM154 and reviewed.
- Historical backup directories are not mistaken for active source.
- Every production code release can be traced to a Git commit/release artifact.
- Ad-hoc production code editing is no longer the normal change path.

**Verification**

- Capture manifest, PR review, deployment release metadata.

### REQ-020 - Reproduce the service from a clean environment

**Requirement**

The repository shall contain enough code, dependency definitions, configuration examples, and deployment automation to make a clean supported VM host the PDU Manager without relying on undocumented production state.

**Acceptance criteria**

- A clean staging VM can be provisioned from repository instructions/automation.
- Application starts and serves the expected UI.
- Required external integrations are documented.
- Manual prerequisites are explicit and minimized.

**Verification**

- Clean staging deployment test.

### REQ-021 - Validate changes automatically in CI

**Requirement**

The project shall run automated CI checks on proposed code changes before merge.

**Acceptance criteria**

- Tests/build/static checks appropriate to the discovered stack run on PRs.
- Secret scanning runs before merge.
- CI does not require dangerous production PDU access.
- Failures block the normal merge path.

**Verification**

- GitHub Actions workflow execution.

### REQ-022 - Deploy reviewed releases through controlled CD

**Requirement**

The project shall support deployment of a human-reviewed, staging-validated release artifact to VM154 through a controlled CD process.

**Acceptance criteria**

- Production deployment uses the exact artifact validated in staging.
- Production deployment requires the configured human approval gate.
- Deployment restarts only required application services when possible, not the entire VM.
- Release identity/commit SHA is recorded.

**Verification**

- Staging deployment and supervised production deployment.

### REQ-023 - Support rollback to a prior known-good release

**Requirement**

The deployment process shall support restoration of the immediately previous known-good application release when post-deploy health checks fail.

**Acceptance criteria**

- Rollback is tested in staging.
- Mutable configuration/state is not lost merely because code is rolled back.
- The rollback procedure is documented and automatable.

**Verification**

- Staging rollback exercise.

---

## Feature: Documentation and Agent Operability

### REQ-024 - Maintain current architecture and operations documentation

**Requirement**

The repository shall document the system sufficiently for another qualified human or AI agent to understand its purpose, boundaries, runtime, deployment, safe operating model, and known limitations without relying on hidden chat history.

**Acceptance criteria**

- `README.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/DEPLOYMENT.md`, and `docs/CURRENT_STATE_AND_LIMITATIONS.md` reflect the current release.
- Significant decisions are recorded in ADRs.
- Accepted/in-progress technical debt is recorded in TDRs.
- Future ideas remain in `FUTURE_WORK.md` until approved.

**Verification**

- PR documentation review.
