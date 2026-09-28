# PDU Manager Deployment

## 1. Current production deployment

**Captured 2026-09-27 from VM154 (all verified live):**

- Host: `MIAM-00133` · Guest: **VM156** (`pdu-control`; production since 2026-09-27) · Endpoint: `https://10.0.20.156/` · VM154 = powered-off fallback
- VM OS: Debian 12 bookworm, kernel 6.1.0-53-cloud-amd64, 16 GiB disk, 1 GiB RAM, timezone America/Chicago, qemu-guest-agent active, sshd PasswordAuthentication=no
- Application runtime: Python 3.11.2 venv at `/opt/pdu-control/venv`; Flask 3.1.3, gunicorn 26.1.0, pexpect 4.9.0, ldap3 2.9.1 (full freeze in `requirements.txt`)
- Live source directory: `/opt/pdu-control` (modules owned root:pducontrol, group-readable)
- Process/service: systemd `pdu-control.service` → gunicorn `--workers 1 --threads 4 --bind 127.0.0.1:5000 --timeout 120 app:app`, `User=pducontrol`, `Restart=always`/`RestartSec=5`, logs → journald
- Reverse proxy/TLS: nginx 1.22.1, `/etc/nginx/sites-available/pdu-control`, TLSv1.2/1.3, self-signed cert `/etc/nginx/ssl/pdu-control.{crt,key}` (CN=10.0.20.154, valid to 2031-09-10, sha256 `137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9`)
- Firewall: nftables `inet pdu_filter` — 22/80/443 allowed from 10.0.20.0/24 + 10.0.10.0/24, 80/443 dropped otherwise
- Dependency manifest: none existed on the VM — `requirements.txt` in this repo is **reconstructed** from the live venv freeze (labeled; validate on staging)
- Application user/group: `pducontrol` (uid 999, system user, nologin, home /opt/pdu-control)
- Persistent config/state: `/etc/pdu-control/{config.json,secrets.env,ARCHITECTURE.md}` + `/var/lib/pdu-control/` + `/var/log/pdu-control/`
- Log location: journald (unit logs) + `/var/log/pdu-control/audit.log{,.jsonl}` (audit; no rotation)
- PDU integration: direct SSH :22 to 10.0.20.151/.152/.153, PowerAlert menu via pexpect, credentials from `PDU_USER_B64`/`PDU_PASS_B64`
- LLDAP/auth: 10.0.20.101:3890, plain LDAP, base `dc=example,dc=com`; service bind `svc-pdu-manager-vm154` (bind-only, no PDU groups)
- Certificate strategy: self-signed, 5-year validity, regenerated on VM (fingerprint pinned in `docs/vm-docs/API.md`); TLS key never in Git
- Restart command: `systemctl restart pdu-control` (application only; VM reboot NOT required for app changes)

## 2. Target deployment principles

1. GitHub is the authoritative application source after live parity validation.
2. Production receives an immutable artifact built from a reviewed Git commit.
3. The same artifact is tested in staging before production.
4. Secrets remain outside Git and outside normal build artifacts.
5. Mutable site configuration remains outside immutable code release directories.
6. Production deploys restart only the application service(s) when possible.
7. Production health checks do not actuate PDU outlets.
8. Failed health checks trigger or permit rapid rollback to the prior known-good release.

## 3. Target directory model

The exact paths may change to match the discovered stack. Unless a stack-native model is better, prefer:

```text
/opt/pdu-manager/
  current -> releases/<git-sha>/
  releases/
    <git-sha>/
/etc/pdu-manager/
  config.*
  secrets.env
/var/lib/pdu-manager/
/var/log/pdu-manager/
```

If the live service is already Dockerized, prefer immutable image digests and external volumes/config rather than forcing this layout.

## 4. Configuration and secrets

Repository may contain:

- config schema;
- safe defaults;
- example PDU endpoints if approved;
- example outlet mappings;
- test fixtures;
- `.env.example` / example config.

Repository must not contain:

- PDU passwords;
- LDAP bind password;
- session secret;
- TLS private key;
- SSH deployment private key;
- runner tokens.

The exact production secret path (verified live): `/etc/pdu-control/secrets.env` — 9 base64-encoded variables (`PDU_USER_B64`, `PDU_PASS_B64`, `SNMP_VERSION_B64`*, `SNMP_RO_B64`*, `SNMP_RW_B64`* — *loaded but unused by live modules*, `WEB_USER_B64`, `WEB_PASS_B64`, `LDAP_SERVICE_USER_B64`, `LDAP_SERVICE_PASS_B64`), permissions 640 root:pducontrol. Placeholders: `deploy/examples/secrets.env.example`.

**VM156 provisioning (FW-004/FW-005):** production secrets/config for the promotion VM are provisioned per `docs/PROVISIONING_VM156.md` — copied from VM154 out-of-Git, ownership 640 root:pducontrol, temp copies shredded. `deploy/set-backend-mode.sh real` refuses to activate the real backend while staging throwaway secrets are still present (fail-safe).

## 5. Clean install contract

A clean supported staging VM must be deployable from repository automation with no undocumented shell history.

Expected deployment phases:

1. install OS prerequisites;
2. create service identity;
3. create code/config/state/log directories;
4. install application artifact;
5. install runtime dependencies;
6. install service/reverse-proxy definitions;
7. install safe staging configuration;
8. validate config;
9. start service;
10. run HTTPS/app health checks;
11. run non-actuating integration tests.

## 6. CI/CD target

Recommended flow:

```mermaid
flowchart LR
    PR[Pull Request] --> CI[CI checks]
    CI --> Review[Human review]
    Review --> Main[Merge to main]
    Main --> Artifact[Immutable artifact]
    Artifact --> Stage[Staging deploy]
    Stage --> Gate[Production approval]
    Gate --> Prod[VM154]
    Prod --> Health[Read-only health check]
    Health -->|fail| Rollback[Previous release]
```

## 7. Self-hosted runner

Because VM154 is on a private `10.0.20.x` network, production deployment will likely require an internal self-hosted runner or equivalent internal deployment agent.

Preferred design:

- dedicated small management VM/CT;
- non-root where feasible;
- deployment-only credentials;
- no PDU actuation credentials;
- access only to the production deployment surface required for VM154;
- protected GitHub Environment for production.

Runner placement/permissions require a human-approved ADR before implementation.

## 8. Rollback contract

Before production CD is enabled, staging must prove that the immediately previous release can be restored without losing mutable outlet configuration.

A rollback must identify:

- target prior release SHA;
- code/runtime dependency set;
- whether configuration/schema changed;
- whether data restoration is necessary;
- health checks after rollback.

## 9. Production deployment runbook — current state

**Status: not yet automated.** Production deploys currently happen by editing files on VM154 (pre-repository era) — that is exactly the practice this migration retires. The captured artifacts give the manual procedure that any automation must replicate:

```bash
# 1. Verify artifact and current release
#    (current release identity = file mtimes on /opt/pdu-control; no release ledger exists yet)

# 2. Backup mutable configuration/state (on VM154, root)
cp -a /etc/pdu-control/config.json /var/backups/pdu-control-config-$(date +%Y%m%d-%H%M%S).json

# 3. Install new immutable release
#    (target: /opt/pdu-control/releases/<git-sha>/ + current symlink — see §3; not yet built)

# 4. Validate config
python3 -c "import json; json.load(open('/etc/pdu-control/config.json'))"

# 5. Switch release and restart application service only
systemctl restart pdu-control

# 6. Read-only health checks
curl -sk https://10.0.20.156/health
curl -sk -o /dev/null -w '%{http_code}\n' https://10.0.20.156/          # 302
curl -sk -o /dev/null -w '%{http_code}\n' https://10.0.20.156/api/v1/health  # 200

# 7. If failed, rollback
#    (V3-era fallback: tarball restore from /root/pdu-control-backups/ + restart; CD-era: prior release + restart)
```

Automating steps 1–7 with an immutable artifact + health gate is the CI/CD plan's scope (sprint: CI/CD Foundation; REQ-021/022/023).
