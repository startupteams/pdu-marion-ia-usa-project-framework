# Source Capture Handoff — PDU Manager (VM154 → GitHub)

**Date:** 2026-09-27
**Issue:** [#1 — Capture live PDU Manager source and establish reproducible deployment baseline](https://github.com/startupteams/pdu-marion-ia-usa-project-framework/issues/1)
**Branches/PRs:** `chore/1-capture-live-pdu-manager` → PR #2 (docs baseline), `feat/1-live-source-capture` → PR #3 (source capture), `feat/1-captured-facts-docs` → PR #4 (captured facts docs) — all merged
**Merges:** PR #2 @ 2026-09-27T12:41:41Z, PR #3, PR #4 — session-scoped self-merge authorization by Jordan (2026-09-27: "automatically merge all the pull requests that you are doing")
**Capture plan of record:** 2026-09-26-PDU-MANAGER-SOURCE-CAPTURE-AND-GITHUB-COMMIT-PLAN.md

## 1. Issue and branch

- Issue #1 (created for this work; issues were disabled on the repo and re-enabled via API).
- `chore/1-capture-live-pdu-manager` — docs baseline package (PR #2, merged).
- `feat/1-live-source-capture` — live source capture (PR #3, merged).
- `feat/1-captured-facts-docs` — captured-facts documentation (PR #4, merged).

## 2. Commit SHAs

| Commit | Content |
|---|---|
| `b512215` | docs baseline package (PR #2) |
| `3afd223` | live source capture: app/ deploy/ config/examples/ docs/vm-docs/ tests/ requirements.txt + CAPTURE_MANIFEST (PR #3) |
| `9b5f0ff` | captured facts into ARCHITECTURE/OPERATIONS/DEPLOYMENT/CURRENT_STATE (PR #4) |
| main after | `edc1f65` (merge of PR #4) |

## 3. PR URLs

- https://github.com/startupteams/pdu-marion-ia-usa-project-framework/pull/2
- https://github.com/startupteams/pdu-marion-ia-usa-project-framework/pull/3
- https://github.com/startupteams/pdu-marion-ia-usa-project-framework/pull/4

## 4. Live source path

`/opt/pdu-control` on VM154 — derived from the running systemd unit (`/etc/systemd/system/pdu-control.service` → `WorkingDirectory=/opt/pdu-control`, gunicorn `app:app`), NOT from `/root` backups. Manifest: `docs/CAPTURE_MANIFEST.md` (per-file SHA-256, classification, exclusions).

## 5. Service unit / process

- `pdu-control.service` (systemd, enabled): `/opt/pdu-control/venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:5000 --timeout 120 --access-logfile - --error-logfile - app:app`, `User=pducontrol`, `Restart=always`/`RestartSec=5`, logs → journald.
- nginx 1.22.1 TLS :443 → 127.0.0.1:5000; :80 → 301 :443. Site: `/etc/nginx/sites-available/pdu-control`. Cert: self-signed `/etc/nginx/ssl/pdu-control.{crt,key}` (valid to 2031-09-10, sha256 `137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9`).
- nftables `inet pdu_filter`: 22/80/443 from 10.0.20.0/24 + 10.0.10.0/24 only.
- No Docker, no DB, no cron/timers for the app.

## 6. Runtime and dependency versions

- VM: Debian 12 bookworm, kernel 6.1.0-53-cloud-amd64, Python 3.11.2, 16 GiB disk, 1 GiB RAM, tz America/Chicago.
- venv (exact freeze → `requirements.txt`): Flask 3.1.3, gunicorn 26.1.0, pexpect 4.9.0, ldap3 2.9.1, Jinja2 3.1.6, Werkzeug 3.1.8, itsdangerous 2.2.0, click 8.4.2, blinker 1.9.0, pyasn1 0.6.4, MarkupSafe 3.0.3, ptyprocess 0.7.0, setuptools 66.1.1, pip 26.2.1.
- **requirements.txt is reconstructed** (no manifest existed on the VM) — validate on staging.

## 7. External dependencies

| Dependency | Detail |
|---|---|
| LLDAP | `10.0.20.101:3890`, plain LDAP, base `dc=example,dc=com`; bind-check auth + `member=` group search (no memberOf); groups pdu-viewer/operator/admin/ai-agent/ai-admin-override |
| PDUs | 10.0.20.151/.152/.153, SSH :22, PowerAlert menu driver (legacy algorithms), REBOOT = native Cycle Load |
| OPNsense | 10.0.20.1 gateway; DHCP reservations for PDUs |
| External monitor | `10.0.20.172` (services.miam.home.arpa) polls / + /login every ~5 min (python-requests) |

## 8. Secret / config strategy

- Runtime secrets: `/etc/pdu-control/secrets.env`, 9 B64 vars, 640 root:pducontrol. Repo: `deploy/examples/secrets.env.example` (placeholders only). TLS key never in Git (cert reproducible via documented openssl command; fingerprint pinned in docs).
- Mutable site config: `/etc/pdu-control/config.json` (outside app tree). Repo: `config/examples/config.example.json` (live mapping, judged SAFE — no credentials).
- **One secret remediation:** captured `docs/vm-docs/HANDOFF.md` contained a legacy PDU-portal Basic-auth credential — removed from Git copy w/ redaction marker; production value untouched (unused by live app). Logged in CAPTURE_MANIFEST.
- Flask session signing key derives from emergency creds → rotating WEB_PASS rotates session signing.

## 9. Staging reproduction result

**App-level reproduction: PASS.** Captured code boots from the repo checkout with fixture-redirected paths (no /etc, no /opt, no network): `/health` 200, `/` 302 → login, `/login` 200, `/api/v1/pdus` 401, `/api/v1/health` 200 (`tests/repro_app_test.py`).

**Full-VM reproduction: NOT yet exercised** (OS → venv → systemd → nginx → TLS → nftables). Deferred into the CI/CD deployment plan's staging work — TDR-0001 stays open. This is recorded honestly per plan §11.

## 10. Validation commands / results

| Check | Command | Result |
|---|---|---|
| Capture tests | `.venv-repro/bin/python -m pytest tests/ -q` | **10 passed** |
| Repro boot | `.venv-repro/bin/python tests/repro_app_test.py` | **PASS** (5/5) |
| Secret scan | regex sweep (sk-/ghp_/github_pat_/private keys/credentialed URLs/password literals) + b64-decode printable sweep | **Clean** (0 real hits; 4 path constants) |
| git hygiene | `git diff --check` | Clean (fixed 2 trailing-blank-line warnings pre-commit) |
| Live service health (read-only) | curl GETs on VM154 | /health 200, / 302, /api/v1/* 401, /api/v1/health 200, login page 200 |
| Production impact | journalctl + nginx log review | Zero functional change; no actuation, no reboots, no config edits |

## 11. Risks and limitations

1. **REQ-003 KVM-label conflict:** live config uses `MIAM-00172 - JetKVM` (153:12) and `MIAM-00173 - KYY 1080p monitor / JetKVM / KVM HDMI splitter` (153:24). The plan-era expected labels (`JetKVM Hardware Console`, `MIAM-00182 - TESmart`) exist only in /root backups, never in live config. Live state treated as truth; REQ-003 wording needs Jordan's review.
2. Reconstructed requirements.txt unvalidated on a clean host.
3. Audit logs have no rotation.
4. `/root` historical backups still on VM (excluded from repo; cleanup = human decision).
5. SNMP_* secrets loaded but unused (cleanup candidate).
6. legacy_app_direct.py retained (pre-V3 monolith, not imported) for archaeology.
7. External monitor (10.0.20.172) is a downstream consumer of unauthenticated UI endpoints.

## 12. ADR / TDR status

- ADR-0001 (Markdown/Mermaid docs): Accepted (pre-existing).
- ADR-0002 (GitHub source of truth, VM154 production target): Accepted, human-directed (PR #2). No new ADRs were required — the capture preserves the live stack as-is; no architecture change was made.
- TDR-0001 (VM not yet reproducible from GitHub): In Progress — app-level repro now proven; full-VM staging repro outstanding.

## 13. Future work discovered

Recorded in `docs/CURRENT_STATE_AND_LIMITATIONS.md` §4/§5: audit-log rotation, requirements.txt staging validation, mock PDU backend for driver tests, release-SHA visibility, config-restore automation, SNMP cleanup, legacy monolith removal. (Not promoted to REQUIREMENTS — human approval required per AGENTS.md.)

## 14. Exact next action for the human reviewer

All three PRs are already merged (session authorization). The next human action is **releasing the CI/CD deployment plan** (second plan received this session): stand up the staging environment from the captured source, add PR CI (safe, non-actuating), immutable artifacts, staging deploy + rollback proof, then protected production deployment to VM154. Before that: review REQ-003 KVM-label wording (see §11.1).