"""PDU Phase 8 tests — reusable client (mock HTTP level, no hardware, no server needed)."""
from __future__ import annotations

import base64
import io
import json
from unittest import mock

import pytest

import pdu_manager_client as pmc


def _client():
    return pmc.PduManagerClient("https://10.0.20.156/api/v1", user="svc-acms",
                                password="pw", verify=False)  # explicit opt-out for tests


def test_tls_verify_default_is_strict():
    c = pmc.PduManagerClient("https://10.0.20.156/api/v1", user="u", password="p")
    assert c._ctx.check_hostname is True
    import ssl as _ssl

    assert c._ctx.verify_mode == _ssl.CERT_REQUIRED


def test_verify_false_is_explicit_only():
    c = _client()
    assert c._ctx.check_hostname is False


def test_identity_mismatch_maps_to_exception():
    c = _client()
    err = mock.Mock()
    err.code = 409
    err.read = lambda: json.dumps({"error": {"code": "TARGET_IDENTITY_MISMATCH", "message": "x"}}).encode()
    with mock.patch("urllib.request.urlopen", side_effect=__import__("urllib").error.HTTPError("u", 409, "c", {}, io.BytesIO(err.read()))):
        with pytest.raises(pmc.PduIdentityMismatch):
            c.submit_action("MIAM-00119", "on", "r", "req-1")


def test_no_auto_new_idempotency_after_timeout():
    """A timeout raises PduError — the client NEVER generates a replacement request_id."""
    c = _client()
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(pmc.PduError):
            c.submit_action("MIAM-00119", "on", "r", "req-original-1")
    # (idempotency key reuse is the CALLER'S decision; the client does not mint keys)


def test_correlation_fields_pass_through():
    c = _client()
    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = json.dumps({"accepted": True, "job_id": "j1"}).encode()
    fake_ctx = mock.MagicMock()
    fake_ctx.__enter__ = mock.Mock(return_value=FakeResp(body))
    fake_ctx.__exit__ = mock.Mock(return_value=False)

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = json.loads(req.data.decode())
        captured["auth"] = req.get_header("Authorization")
        return fake_ctx

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        out = c.submit_action("MIAM-00119", "reboot", "r", "req-9",
                              correlation_id="corr-1", source_service="svc-acms",
                              upstream_operation_id="op-7")
    assert out == {"accepted": True, "job_id": "j1"}
    assert captured["body"]["correlation_id"] == "corr-1"
    assert captured["body"]["source_service"] == "svc-acms"
    assert captured["body"]["upstream_operation_id"] == "op-7"
    assert captured["auth"].startswith("Basic ")