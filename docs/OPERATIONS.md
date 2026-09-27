# PDU Manager Operations Guide

This document explains the operational purpose and safe-use model of the MARION-IA-USA PDU Manager for humans and AI agents.

## 1. Production location

- Proxmox host: `MIAM-00133`
- Guest: VM154
- Web interface: `https://10.0.20.154/`

Exact application service name, runtime path, log path, and restart command must be populated from live source capture and then kept current here.

## 2. What operators use the PDU Manager for

Operators use the web application to:

- see configured PDUs and their outlets;
- translate outlet numbers into asset/load names;
- refresh outlet states;
- deliberately request ON/OFF/REBOOT actions;
- perform selected/batch operations;
- use operational presets;
- recognize protected/locked outlets;
- review recent action results/logs;
- save/upload configuration where the current implementation supports it.

## 3. Normal operator workflow

1. Open `https://10.0.20.154/`.
2. Authenticate if prompted.
3. Identify the PDU and outlet by both outlet number and human-readable label.
4. Use **Refresh States** before acting when current state matters.
5. For a power action, verify the intended target again.
6. Check whether the outlet is protected/locked.
7. Execute only the required action.
8. Confirm the resulting status/action log.

A state refresh must be treated as read-only.

## 4. Critical safety rules

- Do not use ON/OFF/REBOOT as a casual connectivity test.
- Do not bypass outlet protection merely because an action is urgent.
- Do not batch-select outlets without reviewing the entire selection.
- Do not test application changes against real actuation when a mock/staging path exists.
- Do not reboot VM154 merely to change labels or normal application code if restarting the application service is sufficient.
- Do not power-cycle `MIAM-00133` through the PDU Manager except under an explicitly approved recovery procedure.

## 5. KVM-related labels

Current intended labels include:

- `MIAM-00172 - JetKVM Hardware Console`
- `MIAM-00182 - TESmart 16-Port HDMI KVM`

If the UI shows older JetKVM/KYY/HDMI-splitter wording, compare the live configuration to the authoritative hardware architecture before editing. Code deployment must not overwrite current production labels with stale defaults.

## 6. Restart versus VM reboot

A normal PDU Manager application change should normally require only its application service/container to restart.

**Do not default to rebooting VM154.**

After the source capture, replace the placeholders below with exact commands:

```bash
# Status
<exact service status command>

# Restart application only
<exact service restart command>

# Logs
<exact log command/path>
```

If the application cannot be restarted without a VM reboot, document why as technical debt and investigate a normal service lifecycle.

## 7. Troubleshooting checklist

### Web UI is unavailable

1. Confirm VM154 is running from `MIAM-00133`.
2. Confirm expected listener port is present.
3. Check application/reverse-proxy service status.
4. Review recent logs.
5. Check certificate/proxy errors.
6. Do not reboot the VM until service-level recovery has been attempted and the reason for reboot is understood.

### PDU states show unknown/error

1. Confirm the web application itself is healthy.
2. Check network reachability from VM154 to the affected PDU.
3. Check backend logs for authentication/protocol errors.
4. Do not issue repeated power actions to determine whether communication works.
5. Use a read-only state query if supported.

### Authentication fails

1. Verify application health first.
2. Verify connectivity to the configured directory service.
3. Check application auth logs without printing secrets.
4. Do not weaken authentication to restore convenience.

### Outlet label is wrong

1. Compare against the current server architecture inventory.
2. Determine whether the label comes from mutable configuration, seed config, or code.
3. Update the authoritative source/config path.
4. Ensure CI/deployment cannot restore stale labels later.

## 8. Configuration backup/restore

The current UI has exposed save/upload configuration controls. During source capture, document:

- exact file format;
- where the live configuration is stored;
- which fields are mutable;
- whether secrets are included;
- validation performed during import;
- backup/restore procedure;
- compatibility rules across releases.

Do not assume the export is safe to commit until it has been inspected for secrets.

## 9. Agent operating rule

An AI agent working on this project may perform read-only inspection and software validation, but it must not actuate real outlets unless a human explicitly authorizes the exact production action for the session.
