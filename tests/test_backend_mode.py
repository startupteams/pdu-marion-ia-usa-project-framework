# FW-003/FW-004 backend-mode tests — verifies the mock/real selection surface
# WITHOUT any hardware contact. The mock backend import path is exercised; the
# real driver is imported only as a module (no connect() calls ever made).

import importlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _reload_runtime():
    import app_runtime
    importlib.reload(app_runtime)
    return app_runtime


def test_backend_mode_defaults_to_real(monkeypatch):
    """No PDU_BACKEND env -> backend_mode() reports 'real' (the safe default)."""
    monkeypatch.delenv("PDU_BACKEND", raising=False)
    rt = _reload_runtime()
    assert rt.backend_mode() == "real"


def test_backend_mode_mock_env(monkeypatch):
    """PDU_BACKEND=mock -> backend_mode() reports 'mock'."""
    monkeypatch.setenv("PDU_BACKEND", "mock")
    rt = _reload_runtime()
    assert rt.backend_mode() == "mock"


def test_mock_backend_never_imports_real_driver():
    """REQ-010 invariant: in mock mode the real SSH driver module is not imported
    by the runtime's driver reference (the selector swaps it at import time)."""
    monkeypatch = None  # explicit for readability
    import os
    os.environ["PDU_BACKEND"] = "mock"
    try:
        for mod in ("app_runtime", "pdu_worker", "mock_pdu_backend"):
            if mod in sys.modules:
                del sys.modules[mod]
        import app_runtime as rt
        assert rt.backend_mode() == "mock"
        # the runtime driver handle must be the mock backend, never pdu_ssh_direct
        import mock_pdu_backend
        assert rt.pdu_direct is mock_pdu_backend or rt.pdu_direct.__name__ == "mock_pdu_backend"
    finally:
        os.environ.pop("PDU_BACKEND", None)
        for mod in ("app_runtime", "pdu_worker", "mock_pdu_backend"):
            if mod in sys.modules:
                del sys.modules[mod]


def test_health_reports_backend_mode(monkeypatch):
    """/health exposes backend_mode (FW-011 monitoring needs it)."""
    import app
    client = app.app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "ok"
    assert payload["backend_mode"] in ("mock", "real")
    assert len(payload["pdus"]) == 3


def test_set_backend_mode_script_guardrails():
    """set-backend-mode.sh must refuse to activate 'real' with staging secrets
    present (textual guard-rail check — cheap and robust against edits)."""
    script = (REPO_ROOT / "deploy" / "set-backend-mode.sh").read_text()
    assert "STAGING-ONLY" in script or "stage-pass-not-a-real-secret" in script
    assert "refusing to activate real backend" in script
    # must never contain an actual actuation call
    assert "backend.control(" not in script
    assert "run_control_process" not in script


def test_validate_read_only_script_never_actuates():
    """validate-read-only.sh must contain zero power-changing requests."""
    script = (REPO_ROOT / "deploy" / "validate-read-only.sh").read_text()
    forbidden = ["/api/action", "/api/batch", "/actions/batch",
                 "/api/v1/pdus/MIAM-00153/outlets/12/actions",
                 "run_control_process", "backend.control"]
    for needle in forbidden:
        assert needle not in script, f"validate-read-only.sh must not reference {needle}"
