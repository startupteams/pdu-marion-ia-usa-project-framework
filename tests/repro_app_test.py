# Phase F: boot the captured app from the repo checkout with redirected paths,
# prove HTTP endpoints respond read-only. NO PDU actuation (mock-free read paths only).
import base64, builtins, json, os, sys, tempfile, threading, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

tmp = Path(tempfile.mkdtemp(prefix="pdu-repro-"))
shutil.copy(ROOT / "config" / "examples" / "config.example.json", tmp / "config.json")
b64 = lambda s: base64.b64encode(s.encode()).decode()
(tmp / "secrets.env").write_text(
    "PDU_USER_B64=%s\nPDU_PASS_B64=%s\nSNMP_VERSION_B64=%s\nSNMP_RO_B64=%s\nSNMP_RW_B64=%s\n"
    "WEB_USER_B64=%s\nWEB_PASS_B64=%s\nLDAP_SERVICE_USER_B64=%s\nLDAP_SERVICE_PASS_B64=%s\n"
    % (b64("u"), b64("p"), b64("2c"), b64("ro"), b64("rw"), b64("root"), b64("emerg-pass-1"), b64("svc"), b64("svcpass")))
(tmp / "logs").mkdir(); (tmp / "state").mkdir()
(tmp / "logs" / "audit.log").touch()

REDIRECTS = {
    "/etc/pdu-control/config.json": str(tmp / "config.json"),
    "/etc/pdu-control/secrets.env": str(tmp / "secrets.env"),
    "/var/log/pdu-control/audit.log": str(tmp / "logs" / "audit.log"),
    "/var/log/pdu-control/audit.log.jsonl": str(tmp / "logs" / "audit.log.jsonl"),
    "/var/lib/pdu-control/audit.sqlite3": str(tmp / "state" / "audit.sqlite3"),
    "/var/lib/pdu-control/pending_reboot.json": str(tmp / "state" / "pending_reboot.json"),
    "/var/lib/pdu-control/idempotency.json": str(tmp / "state" / "idempotency.json"),
    "/opt/pdu-control/pdu_worker.py": str(ROOT / "app" / "pdu_worker.py"),
    "/opt/pdu-control/venv/bin/python": sys.executable,
}
_real_open = open
builtins.open = lambda f, *a, **k: _real_open(REDIRECTS.get(str(f), f), *a, **k)
_real_makedirs = os.makedirs
def _makedirs(p, *a, **k):
    ps = str(p)
    for prefix, repl in (("/var/lib/pdu-control", str(tmp / "state")),
                         ("/var/log/pdu-control", str(tmp / "logs")),
                         ("/etc/pdu-control", str(tmp)),
                         ("/opt/pdu-control", str(tmp / "opt"))):
        if ps.startswith(prefix):
            ps = repl + ps[len(prefix):]; break
    return _real_makedirs(ps, *a, **k)
os.makedirs = _makedirs

# IMPORTANT: disable startup reconcile thread touching PDUs — it only runs if a
# pending_reboot.json exists, and ours doesn't. State cache stays empty (no SSH).
from app import app

client = app.test_client()

checks = []
r = client.get("/health")
checks.append(("GET /health", r.status_code, r.status_code == 200 and b"backend" in r.data))
r = client.get("/")
checks.append(("GET / unauth", r.status_code, r.status_code == 302))
r = client.get("/login")
checks.append(("GET /login", r.status_code, r.status_code == 200 and b"Sign in" in r.data))
r = client.get("/api/v1/pdus")
checks.append(("GET /api/v1/pdus unauth", r.status_code, r.status_code == 401))
r = client.get("/api/v1/health")
checks.append(("GET /api/v1/health unauth", r.status_code, r.status_code == 200))

ok = True
for name, code, passed in checks:
    print(("PASS" if passed else "FAIL"), name, "->", code)
    ok = ok and passed
print("REPRO_RESULT:", "PASS" if ok else "FAIL")

if __name__ == "__main__":
    sys.exit(0 if ok else 1)
