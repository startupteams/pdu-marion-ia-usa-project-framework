# HANDOFF — PDU Manager LLDAP + AI API Upgrade (V3 Plan)

**Author:** agent_stea004_entrepreneur (GLM 5.3 Flash session, 2026-09-10 → 2026-09-11)
**For:** downstream agents continuing/maintaining the PDU Manager on VM154
**Plan of record:** PDU-MANAGER-LDAP-AI-API-UPGRADE-PLAN-V3.md
**Live URL:** http://10.0.20.154:5000/ (will become https://10.0.20.154/ after Phase 8)

---

## PROGRESS STATUS (updated as work proceeds)

| Phase | Plan section | Status | Notes |
|---|---|---|---|
| 0 Discovery | §4 | ✅ DONE | App at /opt/pdu-control, Flask+gunicorn 1w4t, JSON config at /etc/pdu-control/config.json, audit at /var/log/pdu-control/audit.log |
| 1 Backup | §5 | ✅ DONE | FS backup /root/pdu-control-backups/20260910-222303/ + PVE snapshot `pre_lldap_api_upgrade_20260910` on miam-00133 (task OK) |
| LLDAP discovery | §6 | ✅ DONE | Protocol port **3890** (NOT 17170), base DN `dc=example,dc=com`, plain LDAP no TLS, PVE realm config confirms same values |
| LLDAP groups | §7 | ✅ DONE | pdu-viewer(11) pdu-operator(12) pdu-admin(13) pdu-ai-agent(14) pdu-ai-admin-override(15) created via GraphQL |
| LLDAP accounts | §7/§9 | ✅ DONE | Service bind `svc-pdu-manager-vm154` (lldap_strict_readonly) + test agent `miam_0154_pdu_agent` (pdu-ai-agent + pdu-operator). Passwords set via ldap3 ModifyPassword extop, staged 0600 `~/.pdu_lldap_service_creds` on hermes box, to be written into VM154 secrets.env |
| 2 Action Service | §11/§23 | ✅ CODED | action_service.py + app_runtime.py (actor-aware audit, idempotency, waiting-for-lock queue, self-host pending/reconcile, protected recovery) |
| 3 LLDAP integration | §9 | ✅ CODED | auth_lldap.py: bind-check + member= group search + 60s TTL cache |
| 4 Read-only API | §13 | ✅ CODED | api_v1.py blueprint: /health /me /pdus /outlets |
| 5 API actions | §13 | ✅ CODED | POST /pdus/{ip}/outlets/{n}/actions + /actions/batch + jobs/{id} |
| 6 Override + invariant | §14/§29 | ✅ CODED | Central check_authorization(): protected OFF forbidden for ALL (incl. root/override); protected REBOOT requires override+reason+ack(+self-host ack for 153:9) |
| 7 Idempotency + rate limit | §15/§17 | ✅ CODED | request_id + Idempotency-Key normalized; 24h retention; conflict→409. Rate limiter in api_v1 (120/min GET, 20/min POST per identity) |
| 8 HTTPS + network | §10 | ✅ DONE | nginx 1.22.1 TLS :443 (self-signed, DER sha256 137308d592260184fd75b7a555e27f61332a8c7ffe5feb1f58d1c5553b2221a9), HTTP :80→301, gunicorn bound 127.0.0.1:5000, nftables `inet pdu_filter` allows 80/443+22 from 10.0.20.0/24+10.0.10.0/24, drops 80/443 from all else. Tailscale arrives routed via 10.0.20.x (no tailscale0 iface on VM154) — covered by 10.0.20.0/24 rule. Verified from external host: https health 200, /me 200, http 301 |
| 9 UI integration | §20 | ✅ DONE | /login page (LLDAP + emergency root forms); userbar w/ role chip; override button visible only to override-capable actors (agent: 0 buttons; emergency root: 5). Session flows tested: login 302, dashboard 200, logout, emergency login |
| 10 Docs + cutover | §22/§26 | ✅ DONE — IMPLEMENTATION-REPORT.md, API.md, openapi.json, TEST-RESULTS.md, ROLLBACK.md, CHANGELOG.md, protected-state JSONs deployed to VM154:/opt/pdu-control/docs/ and kept on hermes box ~/pdu_upgrade/ |
| Live test 151:4 | §24.3 | ✅ DONE | Full live sequence over HTTPS: GET state=ON → OFF (202/job) → idempotent replay same job → 409 conflict on rid reuse w/ diff action → verified OFF → ON → verified ON. Cross-restart idempotency verified. Rate limit: 20 POST/min → 429 (live). Protected rejection matrix live: PROTECTED_OFF_FORBIDDEN, SELF_HOST_ACK_REQUIRED, NOT_AUTHORIZED after override-group revocation |
| Protected reboot live | §24.4 | ✅ LOGIC-VERIFIED | Full ack chain enforced live (403s above). Physical protected-outlet cycle NOT executed live (deferred per §24.4 — no safe candidate approved); driver path is the same native '4- Cycle Load' used by every legacy reboot (proven in production, incl. audit rows 2026-09-10/11) |
| Reboot test | §25 | ✅ DONE | VM154 rebooted via PVE API; service auto-started; reconciler ran; API healthy after boot |

---

## ENVIRONMENT FACTS DISCOVERED (read this before touching anything)

- **VM154** = `pdu-control`, qemu on node `miam-00133`, IP 10.0.20.154. Access via PVE API guest-exec (bot account) — no direct SSH key.
- **App stack:** Python 3.11 venv at /opt/pdu-control/venv, Flask 3.1.3, gunicorn 26.1.0 (`--workers 1 --threads 4 --bind 0.0.0.0:5000`), service user `pducontrol`, systemd unit `pdu-control.service` (enabled).
- **Files (live):** app.py (1678 lines, monolith), pdu_ssh_direct.py (1194 lines, pexpect/PowerAlert SSH driver), pdu_worker.py (25-line CLI wrapper the app subprocess-spawns), /etc/pdu-control/config.json, /etc/pdu-control/secrets.env (root-only, B64 values).
- **PDU driver truth:** `reboot` maps to native menu `4- Cycle Load` — **the PDU commits the cycle itself** after a y+ENTER confirmation; the driver closes the SSH session and a fresh session verifies. This satisfies the V3 "atomic native cycle" requirement. Do NOT replace with OFF-then-ON.
- **Protection state (source of truth):** config.json `pdus[2].protected = [3,4,5,6,9]` on MIAM-00153 (firewall, 2.5G, 10G, 1G switch, Proxmox host). MIAM-00151/00152 have `protected: []`. Outlet 153:9 additionally hard-coded in old validate_item as permanently blocked — the new code treats it as a protected outlet + self-host dependency requiring extra ack for REBOOT.
- **First test outlet** MIAM-00151 outlet 4: NOT in labels, NOT protected — safe per plan §1.8.
- **LLDAP:** 10.0.20.101:3890 (LDAP), :17170 (web/GraphQL UI only — do NOT point LDAP clients at 17170). Base `dc=example,dc=com`. Admin bind DN `uid=admin,ou=people,dc=example,dc=com`. **No memberOf attribute** — group membership MUST be found via search `ou=groups,dc=example,dc=com` with `(member=<userDN>)` returning `cn`. No GraphQL mutation for passwords — use ldap3 ModifyPassword extop over :3890 bound as admin.
- **Bot account:** `vm906_agent_stea004_entrepreneur` is a member of `lldap_admin` (full GraphQL user/group management + password resets + PVE API with broad perms). Creds staged 0600 at `~/.pve_ldap_bot` on the hermes box.
- `_ROOT_PW_REMOVED_` for VM/host: staged by Jordan in memory file (see operator); PDU portal Basic-auth credential REMOVED from this Git copy (was present in the original captured doc — see CAPTURE_MANIFEST) staged at a 0600 file on the operator workstation.

---

## DECISIONS MADE (closest-to-V3, autonomous per operator directive)

1. **Session-based UI login** replaces the browser Basic-auth prompt for humans (better logout/roles), while keeping **HTTP Basic for /api/v1 only** (LLDAP creds over TLS). The old root Basic-auth behavior is preserved via the emergency-local login path.
2. **In-memory idempotency** (24h retention, configurable `PDU_IDEMPOTENCY_RETENTION`) instead of SQLite — single gunicorn worker makes this safe, and it preserves the existing process model. Structured audit goes to `audit.log.jsonl` alongside the legacy `audit.log`; SQLite deferred (see open questions).
3. **Rate limiting** implemented per-identity in-process (simple sliding window, 120 GET/min, 20 POST/min) — same worker-process assumption. `429` with Retry-After.
4. **waiting_for_pdu_lock**: API submissions to a busy PDU are queued in-process and drained when the PDU frees (matches V3 §16 semantics). UI submissions keep the old immediate-busy-error behavior.
5. **Self-host (153:9) reboot flow:** job writes `/var/lib/pdu-control/pending_reboot.json`, sends the native cycle with a short settle, returns immediately; `startup_tasks()` on next boot reconciles (reads state; if OFF → recovery ON; clears marker; audits). This matches §14.4 with the caveat that inline verification is impossible (VM loses power).
6. **Emergency-local root** kept for web UI only (login page second form), never for API Basic-auth. Root is audited with `auth_source=emergency-local`.
7. **Throttle on AI destructive actions** before enabling: rate limiter + idempotency both shipped in same release.

---

## BUGS / PROBLEMS ENCOUNTERED (and how handled)

1. **PVE guest-exec urlencode trap** — `urllib.parse.urlencode(body)` without `doseq=True` silently drops the `command` list → exec never runs (HTTP 596 / no pid). ALWAYS urlencode with `doseq=True`. (Known skill pitfall, re-confirmed.)
2. **LLDAP GraphQL introspection** — `user(id:...)` is wrong; the arg is `userId`. Query: `user(userId: "...") { groups { displayName } }`. `createUser` takes a single `user: CreateUserInput!` variable.
3. **ldap3 import path** — `ModifyPassword` lives at `ldap3.extend.standard.modifyPassword` (not `modify_password`). Installed via pip into the agent venv; on VM154 ldap3 must be added to the app venv (`pip install ldap3==2.9.1` — no compiled deps).
4. **No GraphQL password mutation in LLDAP 0.6.x** — passwords set via LDAP password-modify extended operation bound as admin over :3890. Direct writes to SQLite fail (sealed hashes).
5. **Old app hard-blocked 153:9 REBOOT entirely** (old `validate_item`), which conflicts with V3 §14.4 (reboot allowed with ack). New central check enforces: protected→REBOOT allowed only with override+ack; protected→OFF forbidden for everyone incl. override; 153:9 REBOOT additionally requires `acknowledge_controller_may_go_offline`.
6. **Old UI hid OFF/REBOOT buttons for protected outlets** — preserved as-is for viewers/operators; admins now see a REBOOT (override) control gated behind a confirm+reason modal. OFF stays hidden/forbidden for protected outlets for every role (server-enforced too).
7. **gunicorn socket file** `.gunicorn/gunicorn.ctl` in app dir — tar backup prints "socket ignored"; harmless.
8. **VM154 has NO nftables rules and NO nginx yet** — port 5000 currently exposed to any LAN. Phase 8 adds TLS + firewall. Until then, do not enable API in production for agents.

---

## OPEN QUESTIONS / DEFERRED ITEMS

- **SQLite audit DB** (plan §18): shipped JSONL structured log instead; SQLite migration left as a follow-up (files: `/var/lib/pdu-control/audit.sqlite3` planned). JSONL is greppable and sufficient for §18 field list minus indexed queries.
- **HTTPS certificate**: no internal CA discovered yet. Plan: self-signed cert w/ documented fingerprint for agents (curl --cacert), or ACME via OPNsense if Jordan prefers. Decide at Phase 8; default = self-signed + fingerprint pinned in API.md.
- **Tailscale path**: verify whether traffic arrives via tailscale0 interface directly on VM154 (subnet router vs direct). nftables rule set will allow `tailscale0` interface traffic wholesale.
- **Live protected-outlet reboot test** (§24.4): deferred per plan (mocked only) unless a safe candidate is approved later.
- **Rate limits**: starting values only (120/20); tune after agents are onboarded.

---

## CREDENTIAL/SECRET MAP (never print these; 0600 files)

| Secret | Location | Used by |
|---|---|---|
| PVE API bot | `~/.pve_ldap_bot` (hermes box) | pve_api.py / guest-exec |
| Node root pw | `~/.miam_root_pass` (hermes box) | node SSH fallback |
| PDU portal (old Basic) | `~/.pdu_portal` (hermes box) | legacy UI test |
| LLDAP service bind | VM154 `/etc/pdu-control/secrets.env` keys `LDAP_SERVICE_USER_B64`/`LDAP_SERVICE_PASS_B64` (staged from `~/.pdu_lldap_service_creds` on hermes box) | auth_lldap.py group search |
| AI agent test acct | `~/.pdu_lldap_service_creds` key `miam_0154_pdu_agent` (hermes box) | API smoke tests |
| PDU SSH creds | unchanged in `/etc/pdu-control/secrets.env` (PDU_USER_B64/PDU_PASS_B64) | pdu_ssh_direct.py only — never given to agents |

---

## FILES CHANGED / CREATED IN THIS UPGRADE (live paths on VM154)

- `/opt/pdu-control/app_runtime.py` — NEW: shared runtime core (locks, audit, worker, reconcile)
- `/opt/pdu-control/auth_lldap.py` — NEW: LLDAP auth + central authorization (Actor)
- `/opt/pdu-control/action_service.py` — NEW: single shared action path + idempotency + error model
- `/opt/pdu-control/api_v1.py` — NEW: /api/v1 blueprint (health/me/pdus/actions/jobs/batch) + rate limiter
- `/opt/pdu-control/app.py` — REWRITTEN: same UI/UX + login page (LLDAP + emergency root), routes now call the shared action service; legacy /api/action,/api/batch kept for the existing UI JS but routed through the service with actor context
- `/opt/pdu-control/pdu_worker.py` — unchanged interface
- `/opt/pdu-control/pdu_ssh_direct.py` — untouched (physically-proven driver)
- `/etc/pdu-control/secrets.env` — APPENDED keys only (LDAP_SERVICE_*, EMERGENCY_ROOT kept)
- `/var/lib/pdu-control/` — NEW dir (pending_reboot.json lives here)
- systemd unit — unchanged (still binds :5000; Phase 8 moves it to 127.0.0.1 + nginx)

## ROLLBACK

- Restore app: `tar -xzf /root/pdu-control-backups/20260910-222303/pdu-control-app-20260910-222303.tar.gz -C /opt/pdu-control` then `systemctl restart pdu-control`
- Or PVE: `qm rollback 154 pre_lldap_api_upgrade_20260910` on miam-00133 (full VM rollback)
- LLDAP groups/users created are additive; removing them is optional cleanup (groups 11-15, users svc-pdu-manager-vm154, miam_0154_pdu_agent).

---

*Next update: after Phase 8 HTTPS + live tests. If you are reading this mid-implementation, check the STATUS table first; the newest entries are at the top.*
