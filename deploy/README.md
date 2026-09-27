# Deploy tooling — PDU Manager

Scripts in this directory implement the ADR-0003 deployment model (immutable releases + transactional deploys + mock-backend staging).

## Scripts

| Script | Purpose | Run where |
|---|---|---|
| `build-release.sh` | Build immutable `pdu-manager-<sha>.tar.gz` (+ sha256 sidecar); secret-scan gated | CI or workstation |
| `install.sh` | Clean-VM installer (Debian 12): packages, service user, venv, config placeholders, systemd unit, TLS, proxy, firewall. `PDU_STAGING=1` adds mock backend | staging VM / new prod VM (first install) |
| `deploy-release.sh` | Transactional release deploy: checksum → config backup → stage → validate → switch → restart service → health gate → auto-rollback | staging / production VM |
| `healthcheck.sh` | Read-only health checks (service, /health, auth gates, login page; `https` mode adds proxy checks; `full` adds config + log scan). **Never actuates PDUs** | anywhere on the VM |
| `rollback.sh` | Restore previous release + config backup; restart service; health-gated | staging / production VM |

## Secrets

Never in Git. On the VM: `/etc/pdu-control/secrets.env` (from `deploy/examples/secrets.env.example`, all values base64). See docs/DEPLOYMENT.md §4.

## Staging quick start (VM156, 10.0.20.156)

```bash
# from the repo checkout on the workstation
./deploy/build-release.sh
scp dist/pdu-manager-<sha>.tar.gz* jordatech@10.0.20.156:/tmp/
ssh jordatech@10.0.20.156
sudo PDU_STAGING=1 ./deploy/install.sh /tmp/pdu-manager-<sha>.tar.gz
sudo ./deploy/healthcheck.sh http://127.0.0.1:5000 full
# exercise the full action path against the MOCK backend (no hardware):
curl -s -u <staging-user>:<pass> ... # or via the UI at https://10.0.20.156/
```

## Production deploy (after human approval, once runner approved)

```bash
scp dist/pdu-manager-<sha>.tar.gz* root@10.0.20.154:/tmp/   # via approved identity
ssh root@10.0.20.154
./deploy/deploy-release.sh /tmp/pdu-manager-<sha>.tar.gz    # auto-rollback on health failure
```

The mock backend MUST NOT be enabled in production (no `PDU_BACKEND=mock` in the environment).