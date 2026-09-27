# Future Work

This file captures **unapproved ideas, deferred scope, enhancements, and possible future requirements** for the MARION-IA-USA PDU Manager.

Items here are not commitments. Agents must not implement them merely because they are documented. Human approval is required before an item becomes approved scope in `REQUIREMENTS.md`.

## FW-001 - Automatically import asset/outlet mapping from the server architecture source

**Idea**

Add a controlled synchronization/import process from the authoritative server architecture inventory so outlet labels do not require duplicate manual editing.

**Why it may matter**

Reduces drift between hardware documentation and the PDU Manager.

**Notes / dependencies**

Needs conflict handling, human review, and a clear source-of-truth rule before automation.

---

## FW-002 - Formal role-based authorization

**Idea**

Add finer-grained roles such as viewer, operator, and administrator on top of authentication.

**Why it may matter**

Allows read-only users to inspect state without receiving power-control privileges.

**Notes / dependencies**

Current authorization behavior must first be captured and documented.

---

## FW-003 - Two-person approval for critical power-off operations

**Idea**

Require a second authorized approval before OFF/REBOOT actions against explicitly critical devices.

**Why it may matter**

Adds a stronger control against catastrophic operator error.

---

## FW-004 - First-class dry-run/simulation mode

**Idea**

Provide an application mode that executes complete UI/backend flows but replaces hardware actuation with a simulator.

**Why it may matter**

Improves test coverage and agent safety.

---

## FW-005 - PDU health metrics and alerting

**Idea**

Expose metrics and notifications for unreachable PDUs, failed state refreshes, repeated command failures, or abnormal conditions.

**Why it may matter**

Turns troubleshooting from reactive to proactive.

---

## FW-006 - Maintenance windows and scheduled operations

**Idea**

Support approved scheduled power operations/maintenance plans.

**Why it may matter**

Could reduce repetitive manual work for planned maintenance.

**Notes / dependencies**

High-risk feature. Requires explicit safety/authorization design and likely a dedicated ADR.

---

## FW-007 - Public/internal API for other automation systems

**Idea**

Provide a stable authenticated API separate from the browser UI.

**Why it may matter**

Could integrate with LLM Manager, monitoring, maintenance automation, or approved agent workflows.

**Notes / dependencies**

Must not create an easy bypass around protection rules.

---

## FW-008 - High availability / secondary PDU Manager instance

**Idea**

Add a warm standby or reproducible secondary deployment so loss of VM154 does not remove the management interface.

**Why it may matter**

Improves operational resilience.

---

## FW-009 - Centralized deployment/operational metrics

**Idea**

Record release SHA, deploy duration, health-check status, backend error rates, and other observability signals in a centralized dashboard.

**Why it may matter**

Makes production changes easier to audit and troubleshoot.

---

## FW-010 - Replace IP-only access with managed internal DNS/certificate identity

**Idea**

Provide a stable internal DNS name and trusted certificate workflow for the PDU Manager.

**Why it may matter**

Improves operator usability and TLS trust management.

**Notes / dependencies**

Current certificate and DNS infrastructure must be assessed first.
