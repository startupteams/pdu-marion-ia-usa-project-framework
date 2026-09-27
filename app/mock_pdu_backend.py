#!/usr/bin/env python3
"""Mock PDU backend for CI/staging — never touches real hardware.

Implements the same function surface pdu_ssh_direct exposes and is used by
the app when PDU_BACKEND=mock (or when /etc/pdu-control/pdu_backend_mode
says 'mock'). The runtime imports the driver lazily via this selector, so a
staging install can run the FULL app without any PDU reachability.

States: outlets default to 'Off'; ON/OFF/REBOOT mutate an in-memory dict.
"""
import threading
import time

_STATES = {}          # ip -> {outlet: {"state": "On"|"Off", "controllable": True}}
_LOCK = threading.RLock()


def _ensure(ip, outlets=24):
    with _LOCK:
        if ip not in _STATES:
            _STATES[ip] = {i: {"state": "Off", "controllable": True} for i in range(1, outlets + 1)}
        return _STATES[ip]


def connect(ip):
    class _FakeChannel:
        def close(self, force=False):
            pass
    return _FakeChannel()


def read_state(ip, outlet, verbose=False):
    table = _ensure(ip)
    with _LOCK:
        entry = table[int(outlet)]
        return {"state": entry["state"].upper(), "controllable": True, "screen": "MOCK"}


def read_all_states(ip, verbose=False):
    table = _ensure(ip)
    with _LOCK:
        return {int(k): dict(v) for k, v in table.items()}


def control(ip, outlet, action, verbose=False):
    action = action.lower()
    if action not in ("on", "off", "cycle", "reboot"):
        raise ValueError("Action must be on, off, cycle or reboot.")
    table = _ensure(ip)
    with _LOCK:
        entry = table[int(outlet)]
        if action == "on":
            entry["state"] = "On"
        elif action == "off":
            entry["state"] = "Off"
        else:
            entry["state"] = "On"  # cycle ends ON
        final = entry["state"].upper()
    time.sleep(0.05)  # simulate the real settle behavior cheaply
    return final


def navigate_to_outlet(child, outlet, verbose=False):
    return "MOCK"


def parse_state(screen):
    return "MOCK"


def parse_controllable(screen):
    return True