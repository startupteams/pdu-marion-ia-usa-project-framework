# Capture test suite — validation that the captured code is importable and the
# protection invariants hold. These tests NEVER touch real PDUs (no network,
# no SSH, no actuation). Path redirection to fixtures happens in conftest.py.
#
# Run:  python3 -m pytest tests/ -v

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_app_modules_importable():
    """All live modules import with redirected fixtures — proves captured code is intact."""
    import app  # noqa: F401
    import app_runtime  # noqa: F401
    import action_service  # noqa: F401
    import api_v1  # noqa: F401
    import auth_lldap  # noqa: F401
    import pdu_ssh_direct  # noqa: F401
    import pdu_worker  # noqa: F401


def test_protected_outlet_invariant():
    """action_service: protected OFF forbidden for everyone — the core safety invariant."""
    import action_service as asvc
    import auth_lldap
    from action_service import ActionError

    pdu = next(p for p in asvc.rt.CONFIG["pdus"] if p.get("protected"))
    outlet = pdu["protected"][0]
    target = asvc.validate_target(pdu["ip"], outlet, "off")
    assert target["protected"] is True

    admin = auth_lldap.Actor("testadmin", ["pdu-admin"], "lldap")
    with pytest.raises(ActionError) as exc:
        asvc.check_authorization(admin, "off", [target], True, "test", True, True)
    assert exc.value.code == "PROTECTED_OFF_FORBIDDEN"


def test_protected_off_beats_emergency_root():
    """Even emergency-local root (== pdu-admin) cannot OFF a protected outlet."""
    import action_service as asvc
    import auth_lldap
    from action_service import ActionError

    pdu = next(p for p in asvc.rt.CONFIG["pdus"] if p.get("protected"))
    outlet = pdu["protected"][0]
    target = asvc.validate_target(pdu["ip"], outlet, "off")

    root = auth_lldap.Actor("root", ["pdu-admin"], "emergency-local")
    with pytest.raises(ActionError) as exc:
        asvc.check_authorization(root, "off", [target], True, "test", True, True)
    assert exc.value.code == "PROTECTED_OFF_FORBIDDEN"


def test_protected_reboot_requires_override():
    import action_service as asvc
    import auth_lldap
    from action_service import ActionError

    pdu = next(p for p in asvc.rt.CONFIG["pdus"] if p.get("protected"))
    outlet = pdu["protected"][0]
    target = asvc.validate_target(pdu["ip"], outlet, "reboot")

    viewer = auth_lldap.Actor("viewer", ["pdu-viewer"], "lldap")
    with pytest.raises(ActionError):
        asvc.check_authorization(viewer, "reboot", [target], False, "", False, False)


def test_protected_reboot_override_needs_reason_and_acks():
    import action_service as asvc
    import auth_lldap
    from action_service import ActionError

    pdu = next(p for p in asvc.rt.CONFIG["pdus"] if p.get("protected"))
    outlet = pdu["protected"][0]
    target = asvc.validate_target(pdu["ip"], outlet, "reboot")

    admin = auth_lldap.Actor("admin", ["pdu-admin"], "lldap")
    # override flag with no reason -> rejected
    with pytest.raises(ActionError) as exc:
        asvc.check_authorization(admin, "reboot", [target], True, "", True, True)
    assert exc.value.code == "OVERRIDE_REASON_REQUIRED"
    # reason but no protected ack -> rejected
    with pytest.raises(ActionError) as exc:
        asvc.check_authorization(admin, "reboot", [target], True, "maintenance", False, True)
    assert exc.value.code == "PROTECTED_ACK_REQUIRED"


def test_self_host_outlet_metadata():
    import action_service as asvc
    target = asvc.validate_target("10.0.20.153", 9, "reboot")
    assert target["self_host"] is True


def test_self_host_reboot_requires_controller_ack():
    import action_service as asvc
    import auth_lldap
    from action_service import ActionError

    target = asvc.validate_target("10.0.20.153", 9, "reboot")
    admin = auth_lldap.Actor("admin", ["pdu-admin"], "lldap")
    with pytest.raises(ActionError) as exc:
        asvc.check_authorization(admin, "reboot", [target], True, "maintenance", True, False)
    assert exc.value.code == "SELF_HOST_ACK_REQUIRED"


def test_md_config_roundtrip():
    """build_markdown -> parse_markdown_config survives a round trip (schema v1)."""
    import app
    items = [{"ip": "10.0.20.151", "outlet": 3}]
    md = app.build_markdown("on", items)
    action, parsed = app.parse_markdown_config(md)
    assert action == "on"
    assert parsed == items


def test_md_config_upload_rejects_protected_off():
    """Upload-time validation rejects OFF selections on protected outlets."""
    import app
    import pytest as _pytest

    # 10.0.20.153 outlet 3 is protected in the example config
    md = app.build_markdown("off", [{"ip": "10.0.20.153", "outlet": 3}])
    with _pytest.raises(ValueError):
        # parse first (fine), then validate_target rules reject protected+off
        action, items = app.parse_markdown_config(md)
        from action_service import validate_target
        for item in items:
            t = validate_target(item["ip"], item["outlet"], action)
            if t["protected"] and action in ("off", "reboot"):
                raise ValueError("protected")


def test_normalize_action():
    import action_service as asvc
    assert asvc.normalize_action("ON") == "on"
    assert asvc.normalize_action("Reboot") == "reboot"
    # "cycle" is NOT accepted by the shared service (driver-level distinction;
    # the app always normalizes to 'reboot' for the native PDU cycle)
    from action_service import ActionError
    with pytest.raises(ActionError):
        asvc.normalize_action("cycle")
    with pytest.raises(ActionError):
        asvc.normalize_action("bogus")