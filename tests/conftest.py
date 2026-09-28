# Test bootstrap: runs BEFORE any app module import.
#
# The live app hard-codes absolute paths (/etc/pdu-control/config.json etc.)
# and reads them AT IMPORT TIME (app_runtime.py line 45). The captured code is
# intentionally unmodified (this is a capture, not a rewrite), so tests
# redirect those paths using a sitecustomize-style import hook: we create a
# temp fixture tree and patch builtins.open only for those exact paths.

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

# CI/tests NEVER touch the real driver (REQ-010): force the mock backend before any app import.
os.environ.setdefault("PDU_BACKEND", "mock")

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
sys.path.insert(0, str(APP_DIR))

TMP = Path(tempfile.mkdtemp(prefix="pdu-capture-test-"))

_config = TMP / "config.json"
_secrets = TMP / "secrets.env"

import shutil
shutil.copy(REPO_ROOT / "config" / "examples" / "config.example.json", _config)

_b64 = lambda s: base64.b64encode(s.encode()).decode()
_secrets.write_text(
    "PDU_USER_B64=%s\nPDU_PASS_B64=%s\n"
    "WEB_USER_B64=%s\nWEB_PASS_B64=%s\n"
    "LDAP_SERVICE_USER_B64=%s\nLDAP_SERVICE_PASS_B64=%s\n"
    % (_b64("testuser"), _b64("testpass"),
       _b64("root"), _b64("test-emergency"), _b64("svc"), _b64("svcpass"))
)

_log = TMP / "logs"; _state = TMP / "state"
_log.mkdir(); _state.mkdir()
(_log / "audit.log").touch()

REDIRECTS = {
    "/etc/pdu-control/config.json": str(_config),
    "/etc/pdu-control/secrets.env": str(_secrets),
    "/var/log/pdu-control/audit.log": str(_log / "audit.log"),
    "/var/log/pdu-control/audit.log.jsonl": str(_log / "audit.log.jsonl"),
    "/var/lib/pdu-control/audit.sqlite3": str(_state / "audit.sqlite3"),
    "/var/lib/pdu-control/pending_reboot.json": str(_state / "pending_reboot.json"),
    "/var/lib/pdu-control/idempotency.json": str(_state / "idempotency.json"),
    "/opt/pdu-control/pdu_worker.py": str(APP_DIR / "pdu_worker.py"),
    "/opt/pdu-control/venv/bin/python": sys.executable,
}

_real_open = open

def _patched_open(file, *args, **kwargs):
    return _real_open(REDIRECTS.get(str(file), file), *args, **kwargs)

import builtins
builtins.open = _patched_open

# os.makedirs for the state dirs the app may create
_real_makedirs = os.makedirs
def _patched_makedirs(path, *args, **kwargs):
    path_str = str(path)
    for src, dst in REDIRECTS.items():
        if path_str.startswith(os.path.dirname(src)):
            path_str = str(dst) if path_str == src else str(dst)
    # also redirect the well-known state/log directories wholesale
    for prefix, replacement in (
        ("/var/lib/pdu-control", str(_state)),
        ("/var/log/pdu-control", str(_log)),
        ("/etc/pdu-control", str(TMP)),
        ("/opt/pdu-control", str(TMP / "opt")),
    ):
        if path_str.startswith(prefix):
            path_str = replacement + path_str[len(prefix):]
            break
    return _real_makedirs(path_str, *args, **kwargs)
os.makedirs = _patched_makedirs