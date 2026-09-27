#!/usr/bin/env python3

import argparse
import base64
import re
import sys
import time

import pexpect


SECRETS_FILE = "/etc/pdu-control/secrets.env"

PROMPT = r">>\s*"


# ============================================================
# CREDENTIALS
# ============================================================

def load_credentials():

    values = {}

    with open(
        SECRETS_FILE,
        "r",
        encoding="utf-8",
    ) as handle:

        for raw in handle:

            line = raw.strip()

            if not line or "=" not in line:
                continue

            key, value = line.split(
                "=",
                1,
            )

            values[key] = value


    def decode(name):

        encoded = values.get(
            name + "_B64",
            "",
        )

        if not encoded:
            return ""

        return base64.b64decode(
            encoded
        ).decode(
            "utf-8"
        )


    username = decode(
        "PDU_USER"
    )

    password = decode(
        "PDU_PASS"
    )


    if not username:
        raise RuntimeError(
            "PDU username is not configured."
        )

    if not password:
        raise RuntimeError(
            "PDU password is not configured."
        )


    return username, password


# ============================================================
# SSH
# ============================================================

def ssh_args(
    ip,
    username,
):

    return [
        "-tt",

        "-o",
        "StrictHostKeyChecking=no",

        "-o",
        "UserKnownHostsFile=/dev/null",

        "-o",
        "LogLevel=ERROR",

        "-o",
        "PreferredAuthentications=password",

        "-o",
        "PubkeyAuthentication=no",

        "-o",
        "NumberOfPasswordPrompts=1",

        "-o",
        "HostKeyAlgorithms=+ssh-rsa",

        "-o",
        "PubkeyAcceptedAlgorithms=+ssh-rsa",

        "-o",
        (
            "KexAlgorithms="
            "+diffie-hellman-group-exchange-sha1,"
            "diffie-hellman-group1-sha1"
        ),

        "-o",
        (
            "Ciphers="
            "+aes128-cbc,"
            "3des-cbc,"
            "aes256-cbc"
        ),

        f"{username}@{ip}",
    ]


def connect(ip):

    username, password = (
        load_credentials()
    )

    child = pexpect.spawn(
        "ssh",
        ssh_args(
            ip,
            username,
        ),
        encoding="utf-8",
        timeout=12,
    )

    child.delaybeforesend = 0.15


    result = child.expect(
        [
            r"(?i)password:",
            r"(?i)permission denied",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ],
        timeout=12,
    )


    if result != 0:
        raise RuntimeError(
            "Did not reach SSH password prompt."
        )


    # Password prompt belongs to OpenSSH.
    child.sendline(
        password
    )


    result = child.expect(
        [
            PROMPT,
            r"(?i)permission denied",
            r"(?i)password:",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ],
        timeout=15,
    )


    if result != 0:
        raise RuntimeError(
            "SSH login failed or PowerAlert "
            "Main Menu did not appear."
        )


    screen = child.before or ""


    if (
        "Main Menu" not in screen
        or "Devices" not in screen
    ):
        raise RuntimeError(
            "Logged in but did not recognize "
            "PowerAlert Main Menu.\n\n"
            + screen
        )


    return child


# ============================================================
# MENU HELPER
# ============================================================

def send_menu(
    child,
    value,
    timeout=12,
):
    """
    Send a PowerAlert menu selection.

    Old PowerAlert firmware behaves more reliably when the
    printable character and carriage return are sent separately.
    """

    # --------------------------------------------------------
    # NORMAL MENU SELECTION
    # --------------------------------------------------------

    child.send(
        str(value)
    )

    time.sleep(
        0.20
    )

    child.send(
        "\r"
    )

    result = child.expect(
        [
            PROMPT,
            pexpect.TIMEOUT,
            pexpect.EOF,
        ],
        timeout=timeout,
    )

    screen = child.before or ""

    if result == 1:
        raise RuntimeError(
            "Timeout after menu selection "
            f"{value!r}.\n\n"
            + screen
        )

    if result == 2:
        raise RuntimeError(
            "SSH session closed after menu selection "
            f"{value!r}.\n\n"
            + screen
        )


    # --------------------------------------------------------
    # POWER CONTROL CONFIRMATION
    # --------------------------------------------------------

    lower = screen.lower()

    confirmation = (
        "are you sure" in lower
        and (
            "yes, continue and perform operation"
            in lower
            or "do not make change" in lower
        )
    )

    if not confirmation:
        return screen


    print()
    print(
        "PowerAlert confirmation screen detected."
    )

    print()
    print(
        "Sending human-style confirmation:"
    )

    print(
        "  y"
    )

    print(
        "  pause"
    )

    print(
        "  ENTER"
    )


    # IMPORTANT:
    #
    # Do not send b"Y\r" as one burst.
    #
    # Mimic a human terminal session.

    child.send(
        "y"
    )

    time.sleep(
        0.40
    )

    child.send(
        "\r"
    )


    # --------------------------------------------------------
    # DO NOT REQUIRE ANOTHER >> PROMPT
    #
    # Some PowerAlert operations don't immediately redraw the
    # previous menu after a confirmed control.
    #
    # The caller verifies state through an entirely new SSH
    # connection, which is a stronger test anyway.
    # --------------------------------------------------------

    pieces = []

    deadline = (
        time.monotonic()
        + 5.0
    )


    while (
        time.monotonic()
        < deadline
    ):

        try:

            data = child.read_nonblocking(
                size=4096,
                timeout=0.40,
            )

            if data:
                pieces.append(
                    data
                )

        except pexpect.TIMEOUT:
            continue

        except pexpect.EOF:
            pieces.append(
                "\n[SSH SESSION CLOSED BY REMOTE SIDE]\n"
            )

            break


    response = "".join(
        pieces
    )


    print()
    print(
        "PowerAlert response after y + ENTER:"
    )

    print(
        "------------------------------------------------------------"
    )

    if response.strip():
        print(
            response
        )
    else:
        print(
            "[no additional terminal output]"
        )

    print(
        "------------------------------------------------------------"
    )


    return (
        screen
        + "\n"
        + response
    )


# ============================================================
# NAVIGATE TO EXACT OUTLET
# ============================================================

def navigate_to_outlet(
    child,
    outlet,
    verbose=False,
):

    transcript = []


    # --------------------------------------------------------
    # Main Menu
    # 1 = Devices
    # --------------------------------------------------------

    screen = send_menu(
        child,
        "1",
    )

    transcript.append(
        "=== 1 - DEVICES ===\n"
        + screen
    )


    if "5- Loads" not in screen:
        raise RuntimeError(
            "After selecting Devices, expected "
            "'5- Loads' but did not find it.\n\n"
            + screen
        )


    # --------------------------------------------------------
    # Device 1
    # 5 = Loads
    # --------------------------------------------------------

    screen = send_menu(
        child,
        "5",
    )

    transcript.append(
        "=== 5 - LOADS ===\n"
        + screen
    )


    if "1- Configuration" not in screen:
        raise RuntimeError(
            "After selecting Loads, expected "
            "'1- Configuration'.\n\n"
            + screen
        )


    # --------------------------------------------------------
    # Loads
    # 1 = Configuration
    # --------------------------------------------------------

    screen = send_menu(
        child,
        "1",
    )

    transcript.append(
        "=== 1 - CONFIGURATION ===\n"
        + screen
    )


    # Do not guess the exact title here.
    #
    # The next documented operation is selecting a load/outlet.


    # --------------------------------------------------------
    # Select physical outlet
    # --------------------------------------------------------

    screen = send_menu(
        child,
        str(outlet),
    )

    transcript.append(
        f"=== OUTLET {outlet} ===\n"
        + screen
    )


    # This is the critical check.

    if (
        "Controllable" not in screen
        or "Turn Load" not in screen
    ):

        raise RuntimeError(
            "Outlet selection did not reach "
            "Device Load Detail Menu.\n\n"
            "Last screen:\n"
            + screen
            + "\n\nTranscript:\n"
            + "\n\n".join(transcript)
        )


    if verbose:

        print(
            "\n\n".join(
                transcript
            )
        )


    return screen


# ============================================================
# PARSE STATE
# ============================================================

def parse_state(screen):

    match = re.search(
        r"(?im)^\s*State\s*:\s*(On|Off)\s*$",
        screen,
    )

    if match:
        return match.group(1).upper()


    # Backup: the menu itself reveals current state.

    if re.search(
        r"(?i)3-\s*Turn Load Off",
        screen,
    ):
        return "ON"


    if re.search(
        r"(?i)3-\s*Turn Load On",
        screen,
    ):
        return "OFF"


    return "UNKNOWN"


def parse_controllable(screen):

    match = re.search(
        r"(?im)^\s*Controllable\s*:\s*(Yes|No)\s*$",
        screen,
    )

    if not match:
        return None

    return (
        match.group(1).lower()
        == "yes"
    )


# ============================================================
# READ STATE USING FRESH SESSION
# ============================================================

def read_state(
    ip,
    outlet,
    verbose=False,
):

    child = None

    try:

        child = connect(ip)

        screen = navigate_to_outlet(
            child,
            outlet,
            verbose=verbose,
        )

        return {
            "state": parse_state(
                screen
            ),
            "controllable": (
                parse_controllable(
                    screen
                )
            ),
            "screen": screen,
        }


    finally:

        if child is not None:

            try:
                child.close(
                    force=True
                )
            except Exception:
                pass


# ============================================================
# CONTROL
# ============================================================

def control(
    ip,
    outlet,
    action,
    verbose=True,
):

    action = action.lower()

    if action not in (
        "on",
        "off",
        "cycle",
        "reboot",
    ):
        raise ValueError(
            "Action must be on, off, cycle or reboot."
        )


    child = None


    try:

        child = connect(ip)

        screen = navigate_to_outlet(
            child,
            outlet,
            verbose=verbose,
        )


        state = parse_state(
            screen
        )

        controllable = (
            parse_controllable(
                screen
            )
        )


        print()
        print(
            "Current state:",
            state,
        )

        print(
            "Controllable:",
            controllable,
        )


        if controllable is False:
            raise RuntimeError(
                "PowerAlert reports this outlet "
                "as not controllable."
            )


        # ----------------------------------------------------
        # OFF
        # ----------------------------------------------------

        if action == "off":

            if state == "OFF":
                print(
                    "Outlet is already OFF. "
                    "No command necessary."
                )

                return state


            if (
                "Turn Load Off"
                not in screen
            ):
                raise RuntimeError(
                    "Expected menu option "
                    "'3- Turn Load Off' but "
                    "did not find it.\n\n"
                    + screen
                )


            print()
            print(
                "Sending menu option 3: "
                "TURN LOAD OFF"
            )

            send_menu(
                child,
                "3",
            )


        # ----------------------------------------------------
        # ON
        # ----------------------------------------------------

        elif action == "on":

            if state == "ON":

                print(
                    "Outlet is already ON. "
                    "No command necessary."
                )

                return state


            if (
                "Turn Load On"
                not in screen
            ):
                raise RuntimeError(
                    "Expected menu option "
                    "'3- Turn Load On' but "
                    "did not find it.\n\n"
                    + screen
                )


            print()
            print(
                "Sending menu option 3: "
                "TURN LOAD ON"
            )

            send_menu(
                child,
                "3",
            )


        # ----------------------------------------------------
        # CYCLE / REBOOT
        # ----------------------------------------------------

        else:

            if (
                "Cycle Load"
                not in screen
            ):
                raise RuntimeError(
                    "Expected menu option "
                    "'4- Cycle Load' but "
                    "did not find it.\n\n"
                    + screen
                )


            print()
            print(
                "Sending menu option 4: "
                "CYCLE LOAD"
            )

            send_menu(
                child,
                "4",
                timeout=20,
            )


    finally:

        if child is not None:

            try:
                child.close(
                    force=True
                )
            except Exception:
                pass


    # --------------------------------------------------------
    # VERIFY USING A NEW SSH SESSION
    # --------------------------------------------------------

    print()
    print(
        "Waiting for PDU to apply command..."
    )


    if action in (
        "cycle",
        "reboot",
    ):
        time.sleep(6)
    else:
        time.sleep(2)


    result = read_state(
        ip,
        outlet,
        verbose=False,
    )


    final_state = (
        result["state"]
    )


    print()
    print(
        "Verified state:",
        final_state,
    )


    if (
        action == "off"
        and final_state != "OFF"
    ):
        raise RuntimeError(
            "OFF command was sent but "
            f"outlet reports {final_state}."
        )


    if (
        action == "on"
        and final_state != "ON"
    ):
        raise RuntimeError(
            "ON command was sent but "
            f"outlet reports {final_state}."
        )


    if (
        action in (
            "cycle",
            "reboot",
        )
        and final_state != "ON"
    ):
        raise RuntimeError(
            "CYCLE completed but outlet "
            f"reports {final_state}, not ON."
        )


    return final_state


# ============================================================
# COMMAND LINE
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Direct Tripp Lite PowerAlert "
            "SSH menu controller"
        )
    )

    parser.add_argument(
        "ip"
    )

    parser.add_argument(
        "outlet",
        type=int,
    )

    parser.add_argument(
        "action",
        choices=[
            "read",
            "on",
            "off",
            "cycle",
            "reboot",
        ],
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip destructive confirmation",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
    )

    args = parser.parse_args()


    if (
        args.outlet < 1
        or args.outlet > 24
    ):
        raise SystemExit(
            "Outlet must be 1-24."
        )


    if args.action == "read":

        result = read_state(
            args.ip,
            args.outlet,
            verbose=True,
        )

        print()
        print(
            "STATE:",
            result["state"],
        )

        print(
            "CONTROLLABLE:",
            result["controllable"],
        )

        return


    print()
    print("=" * 60)
    print(" REAL PDU POWER OPERATION")
    print("=" * 60)
    print()
    print(
        "PDU:",
        args.ip,
    )
    print(
        "Outlet:",
        args.outlet,
    )
    print(
        "Action:",
        args.action.upper(),
    )
    print()


    # First do an independent read-only check.

    result = read_state(
        args.ip,
        args.outlet,
        verbose=True,
    )

    print()
    print(
        "Current state:",
        result["state"],
    )

    print(
        "Controllable:",
        result["controllable"],
    )


    if not args.yes:

        expected = (
            f"{args.action.upper()} "
            f"{args.ip} "
            f"OUTLET {args.outlet}"
        )

        print()
        print(
            "THIS WILL CHANGE AC POWER."
        )

        print(
            "Type exactly:"
        )

        print()
        print(
            expected
        )

        print()

        entered = input(
            "> "
        )

        if entered != expected:

            print(
                "Confirmation did not match."
            )

            print(
                "No power command sent."
            )

            return


    final = control(
        args.ip,
        args.outlet,
        args.action,
        verbose=False,
    )


    print()
    print("=" * 60)
    print(" SUCCESS")
    print("=" * 60)
    print()
    print(
        "Final outlet state:",
        final,
    )


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print()
        print("=" * 60)
        print(" ERROR")
        print("=" * 60)
        print()
        print(
            str(exc)
        )

        sys.exit(1)

# ============================================================================
# READ-ONLY BULK STATE COMPATIBILITY
#
# Added for Dashboard V2.  This deliberately does NOT change connect(),
# send_menu(), read_state(), control(), or the physically-proven confirmation
# sequence.  It only navigates to the existing PowerAlert Loads Menu and parses
# the ON/OFF table in one SSH session.
# ============================================================================

def _dashboard_parse_load_table(screen):
    import re as _re

    states = {}

    for raw_line in (screen or "").splitlines():
        match = _re.match(
            r"^\s*(\d+)\s+.*?\b(On|Off)\b\s+(Yes|No)\b",
            raw_line,
            flags=_re.IGNORECASE,
        )

        if not match:
            continue

        outlet = int(match.group(1))

        if outlet < 1 or outlet > 64:
            continue

        states[outlet] = {
            "state": match.group(2).upper(),
            "controllable": match.group(3).lower() == "yes",
        }

    return states


def read_all_states(ip):
    """Read every outlet state using ONE read-only PowerAlert SSH session."""

    child = None

    try:
        child = connect(ip)

        # Main Menu -> 1 Devices
        screen = send_menu(child, "1")

        if "5- Loads" not in screen:
            raise RuntimeError(
                "Could not reach the PowerAlert Device menu while reading states.\n\n"
                + (screen or "")
            )

        # Device -> 5 Loads
        screen = send_menu(child, "5")

        if "1- Configuration" not in screen:
            raise RuntimeError(
                "Could not reach the PowerAlert Loads menu while reading states.\n\n"
                + (screen or "")
            )

        # Loads -> 1 Configuration / Loads Menu table
        screen = send_menu(child, "1")

        if (
            "Loads Menu" not in screen
            or "State" not in screen
            or "Control" not in screen
        ):
            raise RuntimeError(
                "PowerAlert returned an unexpected Loads Menu while reading states.\n\n"
                + (screen or "")
            )

        states = _dashboard_parse_load_table(screen)

        if not states:
            raise RuntimeError(
                "No outlet states could be parsed from the PowerAlert Loads Menu.\n\n"
                + (screen or "")
            )

        return states

    finally:
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass
