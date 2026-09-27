# Test for the mock backend + the PDU_BACKEND selection wiring.
# Proves REQ-010's staging guarantee: mock mode can exercise the full action
# path with the real driver unreachable (never imported).

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"


def test_mock_driver_surface():
    sys.path.insert(0, str(APP_DIR))
    import mock_pdu_backend as m

    assert m.control("10.0.20.151", 4, "off") == "OFF"
    assert m.control("10.0.20.151", 4, "on") == "ON"
    assert m.control("10.0.20.151", 4, "reboot") == "ON"
    states = m.read_all_states("10.0.20.151")
    assert states[4]["state"] in ("On", "Off")
    assert states[4]["controllable"] is True
    assert m.read_state("10.0.20.151", 4)["state"] in ("ON", "OFF")
    with pytest.raises(ValueError):
        m.control("10.0.20.151", 4, "explode")


def test_worker_selects_mock_backend():
    """pdu_worker.py with PDU_BACKEND=mock routes to the mock driver."""
    env = dict(os.environ, PDU_BACKEND="mock")
    r = subprocess.run(
        [sys.executable, str(APP_DIR / "pdu_worker.py"), "10.0.20.151", "7", "on"],
        env=env, capture_output=True, text=True, timeout=30,
        cwd=str(APP_DIR),
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["final_state"] == "ON"


def test_app_runtime_mock_selection_real_driver_never_imported():
    """With PDU_BACKEND=mock, app_runtime binds the mock driver and the real
    SSH driver module is never imported (the REQ-010 hardware-isolation guarantee)."""
    tmp = Path(tempfile.mkdtemp(prefix="pdu-mock-sel-"))
    shutil.copy(REPO_ROOT / "config" / "examples" / "config.example.json", tmp / "config.json")
    (tmp / "audit.log").touch()

    script = f"""
import os, sys
os.environ['PDU_BACKEND'] = 'mock'
sys.path.insert(0, {str(APP_DIR)!r})
import builtins
real_open = builtins.open
REDIRECT = {{
    '/etc/pdu-control/config.json': {str(tmp / 'config.json')!r},
    '/var/log/pdu-control/audit.log': {str(tmp / 'audit.log')!r},
    '/var/log/pdu-control/audit.log.jsonl': {str(tmp / 'audit.log.jsonl')!r},
    '/var/lib/pdu-control/pending_reboot.json': {str(tmp / 'pending.json')!r},
    '/opt/pdu-control/pdu_worker.py': {str(APP_DIR / 'pdu_worker.py')!r},
    '/opt/pdu-control/venv/bin/python': sys.executable,
}}
# secrets.env only read lazily; provide empty fallback so reads don't crash
def patched(file, *a, **k):
    p = str(file)
    if p in REDIRECT:
        return real_open(REDIRECT[p], *a, **k)
    if p == '/etc/pdu-control/secrets.env':
        import io
        return io.StringIO('')
    return real_open(file, *a, **k)
builtins.open = patched
import app_runtime
assert app_runtime.pdu_direct.__name__ == 'mock_pdu_backend', app_runtime.pdu_direct.__name__
assert 'pdu_ssh_direct' not in sys.modules, 'REAL DRIVER IMPORTED IN MOCK MODE'
print('MOCK_SELECTION_OK')
"""
    env = dict(os.environ, PDU_BACKEND="mock")
    r = subprocess.run([sys.executable, "-c", script], env=env,
                       capture_output=True, text=True, timeout=30, cwd=str(APP_DIR))
    assert "MOCK_SELECTION_OK" in r.stdout, f"stdout={r.stdout}\nstderr={r.stderr}"