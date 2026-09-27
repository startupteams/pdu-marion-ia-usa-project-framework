# Current State, Implementation Status, and Known Limitations

**Date:** 2026-09-27 (updated post-cutover; VM156 is production)

This file separates what is verified from the live production service, what is implemented-but-not-fully-validated, and what remains open.

## 1. Known current deployment (verified 2026-09-27)

| Item | Current understanding | Status |
|---|---|---|
| Production host | Proxmox `MIAM-00133` | Verified |
| **Production guest (post-cutover 2026-09-27)** | **VM156 (`pdu-manager-staging`, promoted to production), Debian 12** | **Verified** |
| **Production web endpoint** | **`https://10.0.20.156/`** (nginx TLS :443 → gunicorn 127.0.0.1:5000) | **Verified live** |
| Fallback guest | VM154 (`pdu-control`) — POWERED OFF, `onboot=0`, retained untouched as fallback | Verified |
| Managed PDU addresses | `10.0.20.151`, `.152`, `.153` (TripLite MV30HVNet, PowerAlert SSH) | Verified in config + code |
| Backend mode | `real` (PowerAlert SSH driver) — `/health` reports `backend_mode: real`; first real actuation exercised 2026-09-27 (authorized 00119 power-cycle test) | Verified |
| Authentication directory | LLDAP `10.0.20.101:3890`, plain LDAP; UI sessions + emergency-local root; /api/v1 HTTP Basic (LLDAP only — emergency creds rejected by design) | Verified in code + live probes |
| Deployment | Git → CI artifact → protected `production` environment approval → LXC 130 runner → transactional deploy on VM156 (auto-rollback) | Verified E2E (run 36341622843, release `3cd8d89` ACCEPTED) |
| Mapping source of truth | GitHub (this repo), reconciled to the 2026-09-17 spreadsheet (FW-001/FW-002) | Achieved |

## 2. Implemented and verified (live code, 2026-09-27)

All of the following were confirmed in captured code (`app/` in this repo) and, where noted, against the live service:

- Multiple PDU sections (3 PDUs, 24 outlets each, asset_id/name/ip/mac/labels/protected per PDU) — config + UI
- Outlet number + human-readable label display; empty/unassigned outlets distinguishable — UI code + example config
- Outlet state display + refresh; UI polls `/api/status` every 500 ms; refresh is read-only — code
- Individual ON/OFF/REBOOT controls with confirm() dialogs; REBOOT(OVERRIDE) control only for override-capable actors — UI + action service
- Selected/batch actions (max 72 outlets) routed through the shared action service — code
- Presets: 7 built-in selection patterns (non-protected, currently-off, empty ports, Servers 1-2-3-4, Dell 7010s, NUC+DAS, currently-on) + per-PDU patterns — UI JS
- Protection/lockout: `MIAM-00153` outlets 3,4,5,6,9 protected; protected OFF forbidden for EVERYONE (no bypass flag exists); protected REBOOT requires pdu-admin + `admin_override` + non-empty reason + `acknowledge_protected_device`; self-host 153:9 additionally requires `acknowledge_controller_may_go_offline`; batch actions respect protection (all-or-nothing pre-dispatch checks) — action_service.py + live 403 matrix in TEST-RESULTS
- Protected-outlet recovery: unexpected OFF after a protected cycle → recovery ON (2 attempts) — app_runtime.py
- Self-host dependency handling: 153:9 reboot writes pending marker, returns early, reconciles on next startup — app_runtime.py
- Recent-action feedback in UI + DOWNLOAD FULL LOG — UI + audit routes
- Audit logging: dual format (key=value + JSONL), pre-transmission ACCEPTED records, verified_state outcomes, login auditing — app_runtime.py
- Authentication: LLDAP bind-check + `member=` group search (no memberOf), 60 s TTL cache, emergency-local fallback (web only), API Basic — auth_lldap.py + live probes
- Rate limiting: 120 GET/min, 20 POST/min per identity, 429 + Retry-After — api_v1.py + live test evidence
- Idempotency: request_id/Idempotency-Key replay (24 h retention), conflict → 409 — action_service.py + live test evidence
- Configuration save/upload: `.md` schema v1 (machine-readable comments), upload validates structure + rejects protected OFF/REBOOT at upload, never auto-executes — app.py
- Mobile-friendly UI: viewport meta + responsive CSS breakpoints — UI code
- HTTPS: nginx TLSv1.2/1.3, :80 → 301 :443 — live checks
- Firewall: nftables allowlist 10.0.20.0/24 + 10.0.10.0/24 — live config
- `/api/v1` agent API: 8 endpoints, OpenAPI spec captured (`docs/vm-docs/openapi.json`) — live
- External monitor at 10.0.20.172 polls the UI every ~5 min (downstream consumer) — nginx logs

## 3. KVM naming state (reconciled 2026-09-27, FW-001)

**Authoritative Git-managed labels (2026-09-17 architecture spreadsheet, per Future Work v2 §3):**

- Outlet 153:12 → `MIAM-00172 - JetKVM Hardware Console`
- Outlet 153:24 → `MIAM-00182 - TESmart 16-Port HDMI KVM Switch`

**Reconciled 2026-09-27 (FW-001):** the authoritative labels above come from the 2026-09-17 server-architecture spreadsheet (per Future Work v2 §3) and now supersede both the stale production-era labels (`MIAM-00172 - JetKVM` / `MIAM-00173 - KYY 1080p monitor / JetKVM / KVM HDMI splitter`) seen live on VM154 and the backup-only variants. Jordan supplied the spreadsheet mapping authoritatively in the Future Work v2 document, which explicitly resolves the REQ-003 conflict flagged in the capture handoff: the v2 labels are the required Git-managed production labels. After this reconciliation, the GitHub repository is the mapping source of truth (FW-002) unless Jordan supplies a newer authoritative spreadsheet.

Historical note preserved for traceability: the plan-era labels and the 09-06-rev live labels differed; the 09-18 label-update script wrote to a `/root` backup copy, not the live config. That discrepancy is now moot — Git governs the mapping from here.

## 3.1 Outlet-wiring data-quality finding (2026-09-27, authorized test)

During Jordan's explicitly authorized power-cycle test of MIAM-00119, the execution path initially dispatched to the wrong outlet (152:3, labeled MIAM-00135) before correcting to 151:9. Findings:

1. **Power-cut latency is NOT instantaneous** — an outlet verified OFF at the PDU relay can take ~25s+ for the attached node to drop. Treat PDU relay state + 30s wait as the authoritative wiring probe before declaring a mismatch.
2. **`151:9 = MIAM-00119` is CORRECT** — verified end-to-end: OFF dropped the node (ping + PVE API gone), ON restored it (fresh boot), and a PDU REBOOT (native Cycle Load) cleanly rebooted it. All three legs SUCCESS, audit-logged.
3. **`152:3 = MIAM-00135` label is correct** — the node did power-cycle (see §4.11 recovery record).

Recommendation: a full physical outlet-to-asset audit (one careful OFF probe per unlabeled outlet, or ammeter readings) is queued as future work to harden the mapping beyond the spreadsheet import.

## 4. Known limitations / risks today

### 4.11 miam-00135 unplanned power cycles (2026-09-27, recovered)

The authorized 00119 test initially dispatched to 152:3 (= MIAM-00135) due to an execution mix-up; the node was power-cycled twice before the correct outlet was identified. Consequences and recovery:

- All guests on 00135 were stopped after its reboot (PVE `onboot=1` guests did NOT auto-start — start-all timing vs repeated cuts; exact cause not fully diagnosed).
- Recovery executed the same hour: VM114 (LLM Manager prod), VM120 (staging), CT122 (ACMS) started via node SSH.
- ACMS compose stack needed a manual re-raise (`docker compose --env-file /opt/acms/.env -f /opt/acms/repo/deploy/compose.yaml up -d`) — its containers did not auto-start with docker.service.
- Verified healthy after recovery: LLM Manager `http://10.0.20.108/healthz` 200 (v0.11.0 prod baseline, git_sha prod-vm102-correction-20260927), ACMS `https://10.0.20.122/health` `{"status":"ok"}`, VM120 running.
- Stopped-by-default CTs (hermes-* sandboxes, templates) were left untouched — their pre-incident state is not fully known; flagged for Jordan to confirm.
- Lesson: PDU actuation tests must verify the target identity from the authoritative mapping BEFORE dispatch, and default to the least-consequential asset for wiring verification.

### 4.1 Full-VM reproducibility not yet proven

Application-level reproduction is proven (captured code imports, boots, and serves under fixture paths; 10/10 tests). A full clean staging VM deployment (OS → venv → systemd → nginx → TLS → nftables) has NOT yet been exercised. TDR-0001 remains open.

### 4.2 Dependency manifest is reconstructed

No `requirements.txt` existed on the VM. The repo's `requirements.txt` was reconstructed from the live venv freeze (`pip list --format=freeze`) and is labeled reconstructed. Validate on staging before treating as authoritative.

### 4.3 Secrets externalized but manual

All credentials live in `/etc/pdu-control/secrets.env` (B64, 640 root:pducontrol). Provisioning that file on a new VM is a documented manual step (placeholders in `deploy/examples/secrets.env.example`). No secret manager exists.

### 4.4 Historical backups remain on VM154

`/root/` contains bootstrap scripts, 8+ app.py backups, config backups, CLI experiments, and full pre-upgrade snapshots. They are EXCLUDED from the repo capture (classified in `CAPTURE_MANIFEST.md`) but still exist on the VM — cleanup is a future human decision.

### 4.5 CI/CD implemented (2026-09-27, updated)

GitHub Actions CI + immutable artifacts + transactional deploys with auto-rollback are live (CI/CD sprint PRs #7–#9). A protected `production` environment (required reviewer: jordatech) and a production-deploy workflow now exist (Future Work v2 Phase D, PR #14). VM156 runs the current main release with the REAL backend (promotion provisioning 2026-09-27).

### 4.6 Automated real-hardware testing is unsafe and not done

All repo tests run with redirected fixture paths and no network. No test actuates a PDU. The V3-era live rejection-matrix evidence (403s on protected outlets) is preserved in `docs/vm-docs/TEST-RESULTS.md`.

### 4.7 Audit log rotation — repo-side implemented (FW-015, 2026-09-27)

`deploy/logrotate/pdu-control` (weekly, keep 12, compress, copytruncate) ships in the repo; install on a VM is one copy command (documented in OPERATIONS). VM154's local logrotate state is unchanged until cutover — installing there before cutover is unnecessary; VM156 gets it with the next on-box provisioning pass.

### 4.8 Runner trust boundary — ADR-0005 Proposed

A self-hosted deployment runner LXC (130, MIAM-00133) is drafted in ADR-0005 (PROPOSED): scope = repo deploy jobs + VM156 SSH only, no PDU credentials on the runner. Awaiting Jordan's approval; until then the agent-over-SSH path remains the documented deploy mechanism.

### 4.9 SNMP_* secrets — removed from Git-managed surfaces (FW-016, 2026-09-27)

`secrets.env.example`, test fixtures no longer reference SNMP. The live modules never read them. The REAL `SNMP_*` values still exist in `/etc/pdu-control/secrets.env` on VM154 (and thus in the copy on VM156) — removing the vars from production secret files is a maintenance action deferred to cutover coordination (never committed to Git either way).

### 4.10 External monitor dependency

`10.0.20.172` (services.miam.home.arpa) polls `/` + `/login` every ~5 min. Any future auth/proxy change must keep those endpoints reachable or coordinate with the monitor's owner.

## 5. Not yet implemented / not yet proven

- Clean one-command provisioning of a fresh VM (full systemd+nginx+TLS+nftables path)
- Deterministic dependency pinning validated on staging (reconstructed requirements.txt unvalidated on a clean host)
- Mock PDU backend for CI actuation-path tests (current tests cover policy/validation layers, not the driver)
- GitHub Actions CI
- Immutable release artifacts
- Automated staging deployment
- Protected production deployment
- Automatic rollback
- Release-SHA visibility in the UI/health endpoint
- Automated restoration of mutable site configuration after rebuild

## 6. Exit condition for this limitation document

This file should shrink over time. A limitation can be removed only when:

1. the implementation exists;
2. validation is recorded;
3. deployment/operations docs are updated;
4. any related TDR is resolved;
5. a human-reviewed PR has merged.