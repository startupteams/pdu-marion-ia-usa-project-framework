"""REV4 §10C Phase 2 tests — asset-addressed API (mock backend only, no hardware).

Covers: mapping derivation from Git-managed labels, ambiguity detection,
unknown asset 404, protected-policy passthrough (OFF forbidden), action
submission path (mock driver), auth gates (401 first / 403 second).
"""

import base64
import json

import pytest


def _hdr(user="miam_agent", pw="agentpw"):
    import base64

    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """App test client with the mock backend + fake LDAP actor."""
    import os
    import sys

    os.environ["PDU_BACKEND"] = "mock"  # app reads it at IMPORT time (app_runtime.py:34)
    sys.path.insert(0, str(REPO_APP))
    import app as pdu_app
    from auth_lldap import Actor

    # build a synthetic actor with admin group
    class FakeActor:
        username = "miam_agent"
        auth_source = "lldap"

        def can_view(self):
            return True

        def can_control_normal(self):
            return True

        def can_override_protected(self):
            return False

        def can_administer_manager(self):
            return False

        def as_dict(self):
            return {"username": "miam_agent", "groups": ["pdu-ai-agent", "pdu-operator"]}

    monkeypatch.setattr("api_v1.authenticate_lldap", lambda u, p: FakeActor())
    pdu_app.app.config["TESTING"] = True
    return pdu_app.app.test_client()


REPO_APP = None  # set in conftest import path


def test_asset_list_maps_labels(client):
    r = client.get("/api/v1/assets", headers=_hdr())
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["count"] >= 10
    ids = {a["asset_id"] for a in d["data"]}
    assert "MIAM-00119" in ids
    a119 = next(a for a in d["data"] if a["asset_id"] == "MIAM-00119")
    assert a119["pdu_id"] == "MIAM-00151"
    assert a119["outlet"] == 9


def test_asset_get_single(client):
    r = client.get("/api/v1/assets/MIAM-00119", headers=_hdr())
    assert r.status_code == 200
    d = r.get_json()
    assert d["asset_id"] == "MIAM-00119"
    assert d["outlet"] == 9
    assert d["state"] in ("ON", "OFF", "UNKNOWN")


def test_asset_unknown_404(client):
    r = client.get("/api/v1/assets/MIAM-99999", headers=_hdr())
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "ASSET_UNKNOWN"


def test_asset_action_requires_auth(client):
    r = client.post("/api/v1/assets/MIAM-00119/actions", json={"action": "reboot"})
    assert r.status_code == 401  # auth FIRST


def test_asset_action_reboot_queues_mock_job(client):
    r = client.post("/api/v1/assets/MIAM-00119/actions", headers=_hdr(),
                    json={"action": "reboot", "reason": "REV4 E2E acceptance (mock)",
                          "request_id": "rev4-asset-test-001",
                          "correlation_id": "corr-e2e-1", "source_service": "svc-acms",
                          "upstream_operation_id": "ACMS-WORK-1"})
    assert r.status_code == 202, r.data
    d = r.get_json()
    assert d["asset_id"] == "MIAM-00119"
    assert d["resolved"] == {"pdu_id": "MIAM-00151", "outlet": 9}
    assert d["job_id"]


def test_asset_action_off_on_protected_refused(client):
    # find a protected outlet's asset (protection comes from the live config — the example has none
    # protected; this test asserts the POLICY passthrough by forcing a protected set)
    import app_runtime as rt

    rt.CONFIG["pdus"][0]["protected"] = [9]
    try:
        r = client.post("/api/v1/assets/MIAM-00119/actions", headers=_hdr(),
                        json={"action": "off", "reason": "must refuse"})
        assert r.status_code in (400, 403, 409)
        d = r.get_json()
        assert "error" in d
    finally:
        rt.CONFIG["pdus"][0]["protected"] = []


def test_asset_mapping_ambiguity_detected(client, monkeypatch):
    import app_runtime as rt

    rt.CONFIG["pdus"][0]["labels"]["12"] = "MIAM-00119 - DUPLICATE LABEL TEST"
    try:
        r = client.get("/api/v1/assets", headers=_hdr())
        assert r.status_code == 500
        assert r.get_json()["error"]["code"] == "ASSET_MAPPING_AMBIGUOUS"
    finally:
        del rt.CONFIG["pdus"][0]["labels"]["12"]