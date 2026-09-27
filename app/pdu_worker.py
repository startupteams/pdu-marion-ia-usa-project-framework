#!/usr/bin/env python3
import json
import sys

import pdu_ssh_direct


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: pdu_worker.py <ip> <outlet> <on|off|reboot>")

    ip = sys.argv[1]
    outlet = int(sys.argv[2])
    action = sys.argv[3].lower()

    try:
        final_state = pdu_ssh_direct.control(ip, outlet, action)
        print(json.dumps({"ok": True, "final_state": final_state}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
