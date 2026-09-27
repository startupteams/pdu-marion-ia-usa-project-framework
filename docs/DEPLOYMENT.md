# PDU Manager Deployment

## 1. Current production deployment

Known facts:

- Host: `MIAM-00133`
- Guest: VM154
- Endpoint: `https://10.0.20.154/`

The following must be captured from the live VM before this document can be considered complete:

- VM OS/version;
- application runtime/framework;
- exact live source directory;
- process/service/container name;
- reverse proxy/TLS termination;
- dependency manifest;
- application user/group;
- persistent configuration/state directories;
- log location;
- PDU integration configuration;
- LLDAP/authentication configuration;
- certificate location;
- restart command.

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

The exact production secret path must be documented after capture using names/paths only, never values.

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

## 9. Production deployment runbook - placeholder

Replace with exact commands after source capture and staging proof.

```bash
# 1. Verify artifact and current release
<command>

# 2. Backup mutable configuration/state
<command>

# 3. Install new immutable release
<command>

# 4. Validate config
<command>

# 5. Switch release and restart application service only
<command>

# 6. Read-only health checks
<command>

# 7. If failed, rollback
<command>
```

The absence of exact commands is a current readiness gap, not permission to invent them without inspecting VM154.
