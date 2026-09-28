"""REV4 §10C Phases 3-5 tests — identity guard, action-plans (dry-run), capabilities."""

import base64

import pytest


def _hdr():
    tok = base64.b64encode(b"miam_agent:agentpw").decode()
    return {"Authorization": f"Basic {tok}"}


@pytest.fixture()
def client(monkeypatch):
    import os
    import sys

    os.environ.setdefault("PDU_BACKEND", "mock")
    sys.path.insert(0, str(REPO_APP))
    import app as pdu_app

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
            return {"username": "miam_agent"}

    monkeypatch.setattr("api_v1.authenticate_lldap", lambda u, p: FakeActor())
    pdu_app.app.config["TESTING"] = True
    return pdu_app.app.test_client()


REPO_APP = None


def test_identity_guard_mismatch_409_before_dispatch(client):
    """§10C Phase 3: expected_asset_id mismatch fails 409 BEFORE the driver."""
    # outlet 9 on 10.0.20.151 (MIAM-00151) with expected=MIAM-99999 → mismatch
    r = client.post("/api/v1/pdus/10.0.20.151/outlets/9/actions", headers=_hdr(),
                    json={"action": "on", "expected_asset_id": "MIAM-99999"})
    assert r.status_code == 409, r.data
    assert r.get_json()["error"]["code"] == "TARGET_IDENTITY_MISMATCH"


def test_identity_guard_match_passes(client):
    r = client.post("/api/v1/pdus/10.0.20.151/outlets/9/actions", headers=_hdr(),
                    json={"action": "on", "expected_asset_id": "MIAM-00151",
                          "request_id": "rev4-guard-ok-1"})
    assert r.status_code == 202, r.data


def test_action_plan_dry_run_asset(client):
    """Phase 4: plan NEVER actuates — response says actuated=false."""
    r = client.post("/api/v1/action-plans", headers=_hdr(),
                    json={"asset_id": "MIAM-00119", "action": "reboot"})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["actuated"] is False
    assert d["target"]["asset_id"] == "MIAM-00119"
    assert d["target"]["outlet"] == 9
    assert d["target"]["pdu_id"] == "MIAM-00151"
    assert d["caller_authorized"] is True
    assert isinstance(d["required_acknowledgements"], list)


def test_action_plan_protected_off_denied(client, monkeypatch):
    import app_runtime as rt

    rt.CONFIG["pdus"][0]["protected"] = [9]
    try:
        r = client.post("/api/v1/action-plans", headers=_hdr(),
                        json={"asset_id": "MIAM-00119", "action": "off"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["actuated"] is False
        assert d["caller_authorized"] is False
        assert "PROTECTED_OFF_FORBIDDEN" in (d["denial_reason"] or "")
    finally:
        rt.CONFIG["pdus"][0]["protected"] = []


def test_capabilities_endpoint(client):
    r = client.get("/api/v1/capabilities")
    assert r.status_code == 200
    d = r.get_json()
    assert d["api_version"] == "1.0.0"
    assert d["asset_count"] >= 10
    assert "POST /api/v1/action-plans (dry-run)" in d["endpoints"]


def test_version_endpoint(client):
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    assert r.get_json()["git_sha"] in ("unknown",) or len(r.get_json()["git_sha"]) >= 7