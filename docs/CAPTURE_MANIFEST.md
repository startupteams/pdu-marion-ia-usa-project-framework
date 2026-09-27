# PDU Manager — Capture Manifest

**Capture date:** 2026-09-27
**Source:** VM154 (`pdu-control`) on Proxmox node `MIAM-00133`, live production service at `https://10.0.20.154/`
**Capture method:** read-only inspection via QEMU guest agent (`qm guest exec`); live code path derived from the running systemd unit (`/etc/systemd/system/pdu-control.service` → gunicorn `WorkingDirectory=/opt/pdu-control`), **not** from `/root` backup folders.
**Transfer:** tar.gz assembled inside VM154 at `/tmp/pdu-capture-work/capture.tar.gz` (sha256 `54e5a5a3a6d1aaf864a32783da03d7d555dd3852fa7869fb215ddf9a744eeb96`), pulled to the agent workstation via base64 over guest-agent, checksum re-verified after transfer.

## Classification legend

SOURCE / DEPLOYMENT / SAFE CONFIG / SECRET CONFIG / PERSISTENT STATE / GENERATED / BACKUP-LEGACY

## Files captured and committed

| Source path on VM154 | Repo path | SHA-256 | Class | Sanitized | Notes |
|---|---|---|---|---|---|
| /opt/pdu-control/app.py | app/app.py | 954b1494355d…6a550511 | SOURCE | no (clean) | Flask monolith, UI + legacy routes; 21 routes |
| /opt/pdu-control/app_runtime.py | app/app_runtime.py | e9d808113740…8e8866 | SOURCE | no (clean) | runtime core: locks, worker, audit, reconcile |
| /opt/pdu-control/action_service.py | app/action_service.py | d4131c727ad0…85258a4 | SOURCE | no (clean) | central invariant/idempotency |
| /opt/pdu-control/api_v1.py | app/api_v1.py | 6be161ebd66e…a318c3 | SOURCE | no (clean) | /api/v1 blueprint + rate limiter |
| /opt/pdu-control/auth_lldap.py | app/auth_lldap.py | 70cc097976f3…0606482 | SOURCE | no (clean) | LLDAP bind + group search (LDAP ips are site facts, not secrets) |
| /opt/pdu-control/pdu_ssh_direct.py | app/pdu_ssh_direct.py | c237909261aa…4fa25f8da | SOURCE | no (clean) | PowerAlert SSH/pexpect driver |
| /opt/pdu-control/pdu_worker.py | app/pdu_worker.py | e73cc1bca637…928494fb067d0f318 | SOURCE | no (clean) | CLI worker subprocess wrapper |
| /opt/pdu-control/app_direct.py | app/legacy_app_direct.py | b5cade89631c…8aca3ef3b5 | SOURCE (legacy) | no (clean) | pre-V3 monolith; NOT imported by live app.py; kept for archaeology, clearly marked legacy |
| /etc/systemd/system/pdu-control.service | deploy/systemd/pdu-control.service | 22c3a0d84086…b1088e8866 | DEPLOYMENT | no | live unit (gunicorn 1w4t on 127.0.0.1:5000) |
| /etc/nginx/sites-available/pdu-control | deploy/proxy/nginx-pdu-control.conf | ad47f50e571d…edb2eeb2fdd00 | DEPLOYMENT | no | TLS termination config |
| /etc/nftables.conf | deploy/nftables.conf | 2c53f079b9fc…86cad | DEPLOYMENT | no | pdu_filter ruleset |
| /root/pdu-control-bootstrap.sh | deploy/scripts/pdu-control-bootstrap.sh | 62d56177a663…8b86cad | DEPLOYMENT | no | historical bootstrap installer (V1-era; references older paths/labels; NOT the current install method; kept as provenance) |
| /etc/pdu-control/config.json | config/examples/config.example.json | a678fdcc1d29…b704d74 | SAFE CONFIG | renamed to example | live site mapping (asset IDs, IPs, labels, protected list) — judged safe: no credentials, matches published rack map docs |
| /etc/pdu-control/ARCHITECTURE.md | docs/vm-docs/ARCHITECTURE-notes-2026-09-06.md | fbc2dd9b595f…5191273 | SAFE CONFIG (doc) | no | in-VM architecture notes, rev 2026-09-06 |
| /opt/pdu-control/docs/API.md | docs/vm-docs/API.md | da0424b19870…e8866 | DOC | no | agent API guide (contains public TLS fingerprint, not a secret) |
| /opt/pdu-control/docs/CHANGELOG.md | docs/vm-docs/CHANGELOG.md | 629a064251b1…c6ed235ce | DOC | no | V3 upgrade changelog |
| /opt/pdu-control/docs/HANDOFF.md | docs/vm-docs/HANDOFF.md | 4024cf0b4fc4…2f8b86cad→sanitized | DOC | **YES** | one credential string removed (see secret remediation below) |
| /opt/pdu-control/docs/IMPLEMENTATION-REPORT.md | docs/vm-docs/IMPLEMENTATION-REPORT.md | 34e14269bf08…9745b6c4178ae39b12d8dc215f8f99 | DOC | no | V3 implementation report |
| /opt/pdu-control/docs/ROLLBACK.md | docs/vm-docs/ROLLBACK.md | 166bda9ad62a…561cd24c | DOC | no | V3 rollback doc |
| /opt/pdu-control/docs/TEST-RESULTS.md | docs/vm-docs/TEST-RESULTS.md | 16fb8c967cb9…f767e04574ef10465731 | DOC | no | V3 test results (contains rejection-matrix evidence, no creds) |
| /opt/pdu-control/docs/openapi.json | docs/vm-docs/openapi.json | 6ebf810ad276…267c70 | DOC | no | API schema, 8 paths |
| (reconstructed on workstation) | requirements.txt | (new file) | DEPLOYMENT | n/a | reconstructed from live venv `pip freeze`; labeled reconstructed |

## Files inspected but intentionally EXCLUDED

| Source path | Class | Reason |
|---|---|---|
| /etc/pdu-control/secrets.env | SECRET CONFIG | 9 B64-encoded credentials (PDU_USER/PASS, SNMP_VERSION/RO/RW, WEB_USER/PASS, LDAP_SERVICE_USER/PASS). Externalized — see docs/DEPLOYMENT.md §Secrets |
| /opt/pdu-control/venv/ | GENERATED | recreated from requirements.txt |
| /opt/pdu-control/__pycache__/ | GENERATED | bytecode |
| /opt/pdu-control/app.py.backup-*, app.py.before-* | BACKUP/LEGACY | 4 historical copies |
| /opt/pdu-control/pdu_ssh_direct.py.before-confirm-* | BACKUP/LEGACY | 2 historical copies (root-owned 0600) |
| /opt/pdu-control/.gunicorn/ | RUNTIME STATE | gunicorn ctl socket dir |
| /root/pdu-control-secrets.env | SECRET CONFIG | staging copy of secrets |
| /root/pdu-control-backups/ | BACKUP/LEGACY | full pre-upgrade backups incl. secrets.env |
| /root/pdu-control-before-*, *-backup dirs | BACKUP/LEGACY | 4+ snapshot dirs |
| /root/pdu-cli-*.txt, pdu-live-snmp-test.log, poweralert-cli-*.py, test-poweralert-cli-*.py, pdu-telemetry-*.py/txt | BACKUP/LEGACY (discovery artifacts) | historical CLI experiments |
| /root/update-pdu-kvm-labels.sh(.bak) | BACKUP/LEGACY | one-shot label script; already executed its purpose; references labels that differ from live config (see docs/CURRENT_STATE_AND_LIMITATIONS.md §KVM labels) |
| /var/log/pdu-control/audit.log{,.jsonl} | PERSISTENT STATE | live audit trail (contains hostnames/actions; not committed) |
| /var/lib/pdu-control/idempotency.json | PERSISTENT STATE | runtime idempotency cache |
| /var/lib/pdu-control/pending_reboot.json | PERSISTENT STATE | transient marker (absent at capture time) |
| /etc/nginx/ssl/pdu-control.key | SECRET CONFIG | TLS private key — never in Git |
| /etc/nginx/ssl/pdu-control.crt | SAFE CONFIG | public cert; reproducible via documented openssl command; not committed to keep repo deploy-neutral |
| audit.sqlite3 | PERSISTENT STATE | not created on live VM (JSONL is the store) |

## Secret remediation performed

1. **docs/vm-docs/HANDOFF.md line 44**: the original captured document contained a literal PDU portal Basic-auth credential (`root/<password>`). Removed from the Git copy and replaced with a redaction marker; the credential remains unchanged on VM154 (it is not used by the live app — it is a legacy UI portal credential noted in a doc). Logged here per plan §8.
2. **secrets.env**: never captured; externalized to `/etc/pdu-control/secrets.env` (runtime) + `deploy/examples/secrets.env.example` (placeholder names only, matching the 9 live var names, B64-encoded).
3. **Deep scan**: all captured files scanned for `sk-`, `ghp_`, `github_pat_`, private-key headers, credentialed URLs, password-like literal assignments, and long base64 literals that decode to printable text — **0 hits**. The 4 regex hits for `SECRETS_FILE` are path constants, not values.

## Notes

- The live `config.json` uses current labels (`MIAM-00172 - JetKVM`, `MIAM-00173 - KYY 1080p monitor / JetKVM / KVM HDMI splitter`); several `/root` backup copies contain the older `MIAM-00182 - TESmart` naming. Per plan §3, live state wins; backups were not treated as authoritative.
- `app_direct.py` is retained as `app/legacy_app_direct.py` because it is part of the VM's live directory but is not imported by the live app. It is the pre-V3 monolith and serves as historical reference only.