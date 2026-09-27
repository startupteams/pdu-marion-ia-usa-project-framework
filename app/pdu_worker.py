#!/usr/bin/env python3
"""CLI wrapper the app subprocess-spawns for power operations.

Backend selection: PDU_BACKEND=mock routes to the in-repo mock driver
(CI/staging; never touches hardware). Default = real PowerAlert SSH driver.
"""
import json
import os
import sys


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: pdu_worker.py <ip> <outlet> <on|off|reboot>")

    ip = sys.argv[1]
    outlet = int(sys.argv[2])
    action = sys.argv[3].lower()

    if os.environ.get("PDU_BACKEND", "").lower() == "mock":
        import mock_pdu_backend as backend
    else:
        import pdu_ssh_direct as backend

    try:
        final_state = backend.control(ip, outlet, action)
        print(json.dumps({"ok": True, "final_state": final_state}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
