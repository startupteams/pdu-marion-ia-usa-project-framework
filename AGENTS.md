# AGENTS.md

Instructions for autonomous and semi-autonomous coding agents working in this repository.

## 1. Authority model

Humans set product intent, approve scope, approve significant decisions, and approve merges.

Agents may:

- inspect the repository;
- propose a short plan;
- implement approved requirements;
- create or update automated tests;
- update documentation affected by the change;
- add newly discovered ideas to `FUTURE_WORK.md`;
- draft ADRs and TDRs;
- run non-destructive validation;
- prepare commits and pull requests.

Agents must not:

- silently add product scope;
- promote future work into approved requirements without human approval;
- accept their own newly invented significant architecture decision;
- merge their own work under the current operating model;
- commit directly to `main`/production branches;
- use destructive commands, rotate secrets, alter production data, change permissions, or modify infrastructure without explicit human approval;
- hide failed validation, security concerns, or unresolved trade-offs.

## 2. Read before planning

For every meaningful change, read the relevant parts of:

1. `README.md`
2. `REQUIREMENTS.md`
3. `SPRINT.md`
4. `docs/ARCHITECTURE.md`
5. relevant files in `docs/adr/`
6. relevant files in `docs/tdr/`
7. `FUTURE_WORK.md` when scope or follow-up work is involved

Repository-local instructions and approved requirements are the primary source of truth.

## 3. Requirement discipline

Implementation work should trace to one or more `REQ-###` entries.

Before editing, summarize:

- the requirement IDs being implemented;
- the acceptance criteria;
- the files/components likely to change;
- assumptions or ambiguities;
- the validation that will prove the change works.

If the requested work is not covered by an approved requirement, do one of the following:

- ask for human approval to add/modify the requirement; or
- record the idea in `FUTURE_WORK.md` if it is not part of the current scope.

Do not invent scope merely because it seems useful.

## 4. Planning and execution

Before meaningful edits, provide a short implementation plan. Keep the plan proportional to the change.

During execution:

- work in a dedicated branch/worktree/sandbox;
- keep the diff focused;
- follow the existing architecture and project conventions;
- prefer readable, conventional code over clever code;
- avoid unrelated cleanup;
- preserve backward compatibility unless an approved requirement or ADR says otherwise;
- update affected documentation in the same change.

If the implementation grows materially beyond the approved plan, stop and surface the scope increase.

## 5. Significant decisions and ADRs

Create or update an ADR when a change makes a significant, durable decision about architecture, major dependencies, data/storage, external integrations, security boundaries, deployment model, scalability approach, or another choice that future maintainers are likely to ask "why did we choose this?"

### Human-directed decision

If a human explicitly made the decision, the agent may record it as `Status: Accepted`. The ADR must still be called out in the pull request so the human sees what was recorded.

### Agent-originated decision

If the agent concludes a significant decision is needed, create a draft ADR with `Status: Proposed` and surface it for human approval. Do not treat it as approved until a human accepts it.

Use `docs/adr/TEMPLATE.md`.

## 6. Technical debt and TDRs

Create a TDR when a change intentionally leaves known technical debt such as a shortcut, workaround, temporary design, missing test, manual step, or deferred reliability/security/performance/maintainability improvement.

Use `docs/tdr/TEMPLATE.md` and keep the record concise.

An agent may propose a TDR, but the human reviewer should confirm that the debt is acceptable and that the owner is appropriate.

## 7. Validation

Use the exact validation commands documented by the project once the implementation stack is selected.

Run all checks relevant to the change, such as:

- formatting;
- lint/static analysis;
- type checking where applicable;
- unit tests;
- integration tests;
- end-to-end tests where applicable;
- build/package validation;
- security/secret/dependency checks;
- deployment or readiness checks when relevant.

Report exactly what passed, failed, or was not run. Never imply that a check passed if it was skipped or unavailable.

If expected commands are not documented, surface that as an agent-readiness gap instead of guessing silently.

## 8. Security and data handling

- Do not commit secrets, credentials, private keys, access tokens, or sensitive customer/company data.
- Treat generated shell commands, migrations, dependencies, and configuration changes as untrusted until reviewed.
- Do not weaken authentication, authorization, encryption, logging, isolation, or monitoring merely to make a task easier.
- Surface security-sensitive behavior for explicit human review.

## 9. Git and pull requests

Startup Teams Git policy applies unless a stricter project policy is documented.

- Use a dedicated branch; direct commits to `main`/production branches are prohibited.
- Branch format: `type/ticket-short-description` (for example, `feat/AGR-24-bnpl-checkout`).
- Commit format: `type(scope): short description (#issue-id)`.
- Commit scope is required.
- Every commit must reference the related issue/ticket.
- Keep commits and PRs small enough to review.
- Required validation must pass before merge.
- A human reviewer must approve before merge under the current operating model.

Every PR should state:

- feature / requirement IDs;
- what changed and why;
- files/components changed;
- validation performed and results;
- known risks or limitations;
- documentation changes;
- ADRs/TDRs added or changed;
- future-work items discovered;
- reviewer focus areas.

## 10. Stop and ask for human judgment when

- requirements conflict or are materially ambiguous;
- a new significant ADR decision is required;
- credentials or restricted access are required;
- a destructive action is needed;
- tests fail unexpectedly and the cause is not understood;
- security-sensitive behavior changes;
- production data or infrastructure would be changed;
- the diff grows substantially beyond the planned scope;
- a future-work idea would need to become approved product scope.

## 11. Handoff standard

At the end of a work session or before handing work to another agent/human, provide:

1. requirement IDs addressed;
2. concise summary of changes;
3. files changed;
4. validation commands and results;
5. known risks / unresolved questions;
6. ADR/TDR status;
7. future-work items discovered;
8. recommended next action.

## 12. PDU Manager production safety additions

This application can control real electrical power. Treat production interactions as high-consequence infrastructure operations.

Agents MUST NOT perform real PDU ON/OFF/REBOOT/CYCLE operations as tests unless a human explicitly authorizes the exact outlet/action for that session.

During normal development, source capture, CI, staging, or deployment verification:

- use mocks/fixtures for actuation behavior;
- prefer read-only outlet-state checks when technically enforceable;
- do not reboot VM154 or `MIAM-00133` merely to apply application changes;
- restart only the application service(s) when a service restart is sufficient;
- do not modify production outlet labels, presets, protection/lockout settings, or credentials as an incidental test;
- never place PDU/LDAP/TLS credentials in Git;
- preserve protection of critical power targets;
- treat any change that could remove a safety interlock as security/safety sensitive and stop for human review.

The production endpoint is currently `https://10.0.20.154/` on VM154 hosted by `MIAM-00133`. This location is an approved deployment target, not permission for unrestricted infrastructure changes.

## 13. Hardware/outlet mapping authority (FW-002, established 2026-09-27)

The **GitHub repository is the authoritative source of truth for PDU/outlet/hardware mapping** (asset labels, outlet numbering, protected-outlet rules, PDU inventory) once the initial reconciliation from the 2026-09-17 server-architecture spreadsheet (FW-001) has merged.

Rules:

1. **Mapping changes originate in Git** — a PR that updates `config/examples/config.example.json` (and any related docs/tests), reviewed through normal CI, is the only sanctioned path.
2. **A newer spreadsheet or mapping supplied explicitly by Jordan supersedes Git** and triggers a reviewed reconciliation PR — not an unmanaged production edit.
3. **Production UI must not be used to casually edit hardware mapping.** After the VM156 cutover, mapping changes flow Git → approved release → VM156. Direct edits on a production VM are emergency-only (break-glass: document why, preserve pre-change state, minimum change, verify safely, back-port to Git immediately).
4. **Protection rules are part of the mapping** — changing which outlets are protected is safety-sensitive and requires human review of the PR regardless of how small the diff is.
5. Agents must never "helpfully" import labels from VM backups, old scripts, or stale docs — only from the current Git configuration or a Jordan-designated authoritative source.

The 2026-09-17 spreadsheet import basis: outlet `MIAM-00153:12` = `MIAM-00172 - JetKVM Hardware Console`; outlet `MIAM-00153:24` = `MIAM-00182 - TESmart 16-Port HDMI KVM Switch` (Future Work v2 §3).
