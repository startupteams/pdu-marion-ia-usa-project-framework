#!/usr/bin/env python3

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import threading
import time

import pdu_ssh_direct as pdu_direct

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)


# ============================================================
# PATHS
# ============================================================

CONFIG_FILE = "/etc/pdu-control/config.json"
SECRETS_FILE = "/etc/pdu-control/secrets.env"
AUDIT_FILE = "/var/log/pdu-control/audit.log"


# ============================================================
# CONFIGURATION
# ============================================================

with open(
    CONFIG_FILE,
    "r",
    encoding="utf-8",
) as handle:

    CONFIG = json.load(handle)


def load_secret_file():

    values = {}

    with open(
        SECRETS_FILE,
        "r",
        encoding="utf-8",
    ) as handle:

        for raw in handle:

            line = raw.strip()

            if (
                not line
                or "=" not in line
            ):
                continue

            key, value = line.split(
                "=",
                1,
            )

            values[key] = value

    return values


RAW_SECRETS = load_secret_file()


def secret(name):

    encoded = RAW_SECRETS.get(
        f"{name}_B64",
        "",
    )

    if not encoded:
        return ""

    try:

        return base64.b64decode(
            encoded
        ).decode(
            "utf-8"
        )

    except Exception:

        return ""


PDU_USER = secret("PDU_USER")
PDU_PASS = secret("PDU_PASS")

WEB_USER = (
    secret("WEB_USER")
    or "admin"
)

WEB_PASS = secret("WEB_PASS")


if not PDU_USER or not PDU_PASS:

    raise RuntimeError(
        "PDU SSH credentials are not configured."
    )


if not WEB_PASS:

    raise RuntimeError(
        "Dashboard web password is not configured."
    )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

app.secret_key = hashlib.sha256(
    (
        "pdu-control:"
        + WEB_USER
        + ":"
        + WEB_PASS
    ).encode(
        "utf-8"
    )
).digest()


# ============================================================
# SERIALIZE ACCESS PER PDU
#
# Gunicorn uses multiple threads. Old PowerAlert cards are much
# happier if two operations are not performed simultaneously
# against the same management card.
# ============================================================

PDU_LOCKS = {
    pdu["ip"]: threading.Lock()
    for pdu in CONFIG["pdus"]
}


def get_pdu_lock(ip):

    if ip not in PDU_LOCKS:
        PDU_LOCKS[ip] = threading.Lock()

    return PDU_LOCKS[ip]


# ============================================================
# AUTHENTICATION
# ============================================================

def authentication_failure():

    return Response(
        "Authentication required",
        401,
        {
            "WWW-Authenticate":
                'Basic realm="MIAM PDU Control"'
        },
    )


@app.before_request
def authenticate():

    auth = request.authorization

    if not auth:
        return authentication_failure()

    username_ok = hmac.compare_digest(
        auth.username or "",
        WEB_USER,
    )

    password_ok = hmac.compare_digest(
        auth.password or "",
        WEB_PASS,
    )

    if not username_ok or not password_ok:
        return authentication_failure()

    return None


# ============================================================
# AUDIT
# ============================================================

def audit(message):

    timestamp = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    source = (
        request.remote_addr
        if request
        else "unknown"
    )

    line = (
        f"{timestamp} "
        f"user={WEB_USER} "
        f"source={source} "
        f"{message}\n"
    )

    with open(
        AUDIT_FILE,
        "a",
        encoding="utf-8",
    ) as handle:

        handle.write(line)


def recent_audit(lines=30):

    try:

        with open(
            AUDIT_FILE,
            "r",
            encoding="utf-8",
        ) as handle:

            content = handle.readlines()

        return "".join(
            content[-lines:]
        )

    except FileNotFoundError:

        return (
            "No actions have been "
            "performed yet.\n"
        )


# ============================================================
# CONFIG HELPERS
# ============================================================

def find_pdu(ip):

    for pdu in CONFIG["pdus"]:

        if pdu["ip"] == ip:
            return pdu

    return None


def backend_description():

    return (
        "Direct SSH menu control "
        "(verified PowerAlert sequence)"
    )


# ============================================================
# FAST STATE READER
#
# IMPORTANT:
#
# Do NOT open one SSH session for every outlet.
#
# We navigate to:
#
#   Main Menu
#     -> 1 Devices
#     -> 5 Loads
#     -> 1 Configuration
#
# That one screen already contains the state of all 24 outlets.
#
# Therefore a dashboard refresh requires only ONE SSH session
# per PDU.
# ============================================================

def parse_load_table(screen):

    states = {}

    for raw_line in screen.splitlines():

        parts = raw_line.split()

        if not parts:
            continue

        if not parts[0].isdigit():
            continue

        outlet = int(
            parts[0]
        )

        if outlet < 1 or outlet > 64:
            continue

        state = None
        controllable = None

        for index in range(
            1,
            len(parts),
        ):

            token = parts[index].lower()

            if token in (
                "on",
                "off",
            ):

                state = token.upper()

                for candidate in parts[
                    index + 1:
                ]:

                    lower = (
                        candidate.lower()
                    )

                    if lower == "yes":

                        controllable = True
                        break

                    if lower == "no":

                        controllable = False
                        break

                break

        if state:

            states[outlet] = {
                "state": state,
                "controllable": controllable,
            }

    return states


def read_all_states_unlocked(ip):

    child = None

    try:

        child = pdu_direct.connect(
            ip
        )

        # Main Menu -> Devices
        screen = pdu_direct.send_menu(
            child,
            "1",
        )

        if "5- Loads" not in screen:

            raise RuntimeError(
                "Could not reach Device menu."
            )

        # Device -> Loads
        screen = pdu_direct.send_menu(
            child,
            "5",
        )

        if "1- Configuration" not in screen:

            raise RuntimeError(
                "Could not reach Loads menu."
            )

        # Loads -> Configuration
        screen = pdu_direct.send_menu(
            child,
            "1",
        )

        if (
            "Loads Menu" not in screen
            or "Load" not in screen
            or "State" not in screen
            or "Control" not in screen
        ):

            raise RuntimeError(
                "PowerAlert returned an "
                "unexpected Loads Menu."
            )

        states = parse_load_table(
            screen
        )

        if not states:

            raise RuntimeError(
                "No outlet states could be "
                "parsed from Loads Menu."
            )

        return states

    finally:

        if child is not None:

            try:

                child.close(
                    force=True
                )

            except Exception:

                pass


def read_all_states(ip):

    lock = get_pdu_lock(
        ip
    )

    with lock:

        return read_all_states_unlocked(
            ip
        )


# ============================================================
# VERIFIED POWER CONTROL
#
# This calls the exact pdu_ssh_direct.control() implementation
# that successfully powered PDU .152 outlet 7 OFF.
#
# No alternate SSH implementation is maintained here.
# ============================================================

def perform_action(
    ip,
    outlet,
    action,
):

    if action not in (
        "on",
        "off",
        "reboot",
    ):

        raise RuntimeError(
            "Invalid power action."
        )

    lock = get_pdu_lock(
        ip
    )

    with lock:

        final_state = (
            pdu_direct.control(
                ip,
                outlet,
                action,
                verbose=False,
            )
        )

    return (
        "Direct SSH",
        final_state,
    )


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html>
<head>

<meta charset="utf-8">

<title>
    MIAM Rack PDU Control
</title>

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 28px;
    background: #151622;
    color: #e8eaf3;

    font-family:
        Inter,
        Arial,
        Helvetica,
        sans-serif;
}

.container {
    max-width: 1450px;
    margin: auto;
}

h1 {
    margin:
        0
        0
        4px
        0;

    font-size: 32px;
}

.subtitle {
    color: #aeb3c4;
    margin-bottom: 16px;
}

.topbar {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 24px;
}

.mode {
    display: inline-block;

    background: #222536;
    border: 1px solid #3a3f55;

    padding: 8px 12px;

    border-radius: 8px;

    font-size: 13px;
    color: #9fd0ff;
}

.refresh {
    display: inline-block;

    text-decoration: none;

    background: #41475f;
    color: white;

    border-radius: 8px;

    padding: 8px 14px;

    font-size: 13px;
    font-weight: 700;
}

.flash {
    padding: 13px 16px;
    margin-bottom: 16px;

    border-radius: 8px;

    background: #30354a;
    border: 1px solid #50566f;

    white-space: pre-wrap;
}

.pdu {
    background: #242635;

    border: 1px solid #35384b;

    border-radius: 12px;

    margin-bottom: 28px;

    overflow: hidden;
}

.pdu-header {
    min-height: 64px;

    display: flex;
    align-items: center;
    justify-content: space-between;

    padding: 0 22px;

    background: #303348;
}

.pdu-title {
    font-size: 21px;
    font-weight: 800;
    color: #7eb2ff;
}

.ip {
    font-family: monospace;
    color: #c2c8dc;
}

.pdu-error {
    padding: 10px 22px;

    background: #4d2d35;
    color: #ffd1d9;

    font-size: 13px;
}

.outlet {
    min-height: 56px;

    display: grid;

    grid-template-columns:
        82px
        minmax(300px, 1fr)
        110px
        310px;

    align-items: center;

    gap: 0;

    padding: 0 20px;

    border-top: 1px solid #343748;
}

.number {
    font-family: monospace;
    font-size: 12px;
    color: #b8c9eb;
}

.label {
    font-weight: 700;
    padding-right: 20px;
}

.state {
    font-family: monospace;
    font-weight: 900;
    font-size: 13px;
}

.state.ON {
    color: #7ee8a1;
}

.state.OFF {
    color: #ff8297;
}

.state.UNKNOWN {
    color: #f0c45e;
}

.controls form {
    display: flex;
    align-items: center;
    gap: 8px;
}

button {
    border: 0;

    border-radius: 6px;

    padding: 10px 15px;

    font-size: 13px;
    font-weight: 900;

    cursor: pointer;
}

button:hover {
    filter: brightness(1.08);
}

button.on {
    background: #81dfa0;
    color: #111827;
}

button.off {
    background: #fb839b;
    color: #111827;
}

button.reboot {
    background: #f4ce73;
    color: #111827;
}

.protected {
    color: #ffb3c0;

    font-size: 12px;
    font-weight: 800;
}

.audit {
    margin-top: 35px;

    padding: 20px;

    background: #1d1f2c;

    border: 1px solid #343748;

    border-radius: 10px;
}

.audit h2 {
    margin-top: 0;
}

.audit pre {
    overflow-x: auto;

    color: #c7ccda;

    white-space: pre-wrap;

    font-size: 12px;
}

.note {
    color: #aeb3c4;
    font-size: 12px;
    margin-bottom: 20px;
}

@media (
    max-width: 950px
) {

    .outlet {
        grid-template-columns:
            70px
            1fr;

        padding:
            12px
            16px;

        row-gap: 10px;
    }

    .state,
    .controls {
        grid-column: 2;
    }
}

</style>

</head>

<body>

<div class="container">

    <h1>
        Rack PDU Power Control Center
    </h1>

    <div class="subtitle">
        Persistent PDU controller hosted in Proxmox VM 154
    </div>

    <div class="topbar">

        <div class="mode">
            Backend: {{ backend }}
        </div>

        <a
            class="refresh"
            href="/"
        >
            REFRESH STATES
        </a>

    </div>

    <div class="note">
        Each refresh reads all outlets using one SSH session per PDU.
        OFF and REBOOT remain disabled for configured protected outlets.
    </div>


    {% with messages = get_flashed_messages() %}

        {% if messages %}

            {% for message in messages %}

                <div class="flash">
                    {{ message }}
                </div>

            {% endfor %}

        {% endif %}

    {% endwith %}


    {% for pdu in pdus %}

        <div class="pdu">

            <div class="pdu-header">

                <div class="pdu-title">
                    {{ pdu.name }}
                </div>

                <div class="ip">
                    {{ pdu.ip }}
                </div>

            </div>


            {% if pdu.error %}

                <div class="pdu-error">
                    STATE READ ERROR:
                    {{ pdu.error }}
                </div>

            {% endif %}


            {% for row in pdu.rows %}

                <div class="outlet">

                    <div class="number">
                        Outlet {{ row.number }}
                    </div>

                    <div class="label">
                        {{ row.label }}
                    </div>

                    <div
                        id="state-{{ pdu.ip|replace('.', '-') }}-{{ row.number }}"
                        class="state {{ row.state }}"
                    >
                        {{ row.state }}
                    </div>

                    <div class="controls">

                        <form
                            action="/action"
                            method="POST"
                        >

                            <input
                                type="hidden"
                                name="ip"
                                value="{{ pdu.ip }}"
                            >

                            <input
                                type="hidden"
                                name="outlet"
                                value="{{ row.number }}"
                            >


                            <button
                                class="on"
                                name="action"
                                value="on"
                                onclick="
                                    return confirm(
                                        'Turn ON {{ pdu.name }} / Outlet {{ row.number }} / {{ row.label|e }}?'
                                    );
                                "
                            >
                                ON
                            </button>


                            {% if not row.protected %}

                                <button
                                    class="off"
                                    name="action"
                                    value="off"
                                    onclick="
                                        return confirm(
                                            'TURN OFF {{ pdu.name }} / Outlet {{ row.number }} / {{ row.label|e }}?'
                                        );
                                    "
                                >
                                    OFF
                                </button>

                                <button
                                    class="reboot"
                                    name="action"
                                    value="reboot"
                                    onclick="
                                        return confirm(
                                            'HARD POWER-CYCLE {{ pdu.name }} / Outlet {{ row.number }} / {{ row.label|e }}?'
                                        );
                                    "
                                >
                                    REBOOT
                                </button>

                            {% else %}

                                <span class="protected">
                                    🔒 OFF / REBOOT PROTECTED
                                </span>

                            {% endif %}

                        </form>

                    </div>

                </div>

            {% endfor %}

        </div>

    {% endfor %}


    <div class="audit">

        <h2>
            Recent Actions
        </h2>

        <pre>{{ audit }}</pre>

    </div>

</div>

</body>
</html>
"""


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/")
def index():

    view_pdus = []

    for pdu in CONFIG["pdus"]:

        error = None

        try:

            states = read_all_states(
                pdu["ip"]
            )

        except Exception as exc:

            states = {}
            error = str(exc)

        protected = {
            int(value)
            for value in pdu.get(
                "protected",
                [],
            )
        }

        labels = pdu.get(
            "labels",
            {},
        )

        rows = []

        for outlet in range(
            1,
            int(
                pdu.get(
                    "outlets",
                    24,
                )
            ) + 1,
        ):

            info = states.get(
                outlet,
                {},
            )

            rows.append(
                {
                    "number":
                        outlet,

                    "label":
                        labels.get(
                            str(outlet),
                            f"Outlet {outlet}",
                        ),

                    "state":
                        info.get(
                            "state",
                            "UNKNOWN",
                        ),

                    "controllable":
                        info.get(
                            "controllable",
                            None,
                        ),

                    "protected":
                        outlet
                        in protected,
                }
            )

        view_pdus.append(
            {
                "name":
                    pdu["name"],

                "ip":
                    pdu["ip"],

                "rows":
                    rows,

                "error":
                    error,
            }
        )

    return render_template_string(
        HTML,
        pdus=view_pdus,
        backend=backend_description(),
        audit=recent_audit(),
    )


# ============================================================
# WEB POWER ACTION
# ============================================================

@app.post("/action")
def action():

    ip = request.form.get(
        "ip",
        "",
    )

    action_name = request.form.get(
        "action",
        "",
    ).lower()

    try:

        outlet = int(
            request.form.get(
                "outlet",
                "0",
            )
        )

    except ValueError:

        flash(
            "Invalid outlet number."
        )

        return redirect(
            url_for("index")
        )


    pdu = find_pdu(
        ip
    )

    if not pdu:

        flash(
            "Unknown PDU."
        )

        return redirect(
            url_for("index")
        )


    if action_name not in (
        "on",
        "off",
        "reboot",
    ):

        flash(
            "Invalid action."
        )

        return redirect(
            url_for("index")
        )


    max_outlets = int(
        pdu.get(
            "outlets",
            24,
        )
    )


    if (
        outlet < 1
        or outlet > max_outlets
    ):

        flash(
            "Outlet is outside "
            "the configured range."
        )

        return redirect(
            url_for("index")
        )


    protected = {
        int(value)
        for value in pdu.get(
            "protected",
            [],
        )
    }


    if (
        outlet in protected
        and action_name in (
            "off",
            "reboot",
        )
    ):

        message = (
            f"BLOCKED: {pdu['name']} "
            f"outlet {outlet} "
            f"is protected."
        )

        audit(
            message
        )

        flash(
            message
        )

        return redirect(
            url_for("index")
        )


    label = pdu.get(
        "labels",
        {},
    ).get(
        str(outlet),
        f"Outlet {outlet}",
    )


    try:

        backend, final_state = (
            perform_action(
                ip,
                outlet,
                action_name,
            )
        )


        message = (
            f"{action_name.upper()} SUCCESS: "
            f"{pdu['name']} / "
            f"Outlet {outlet} / "
            f"{label}. "
            f"Verified state: "
            f"{final_state}."
        )


        audit(
            f'pdu="{pdu["name"]}" '
            f'ip={ip} '
            f'outlet={outlet} '
            f'asset="{label}" '
            f'action={action_name.upper()} '
            f'backend="{backend}" '
            f'verified_state={final_state} '
            f'result=SUCCESS'
        )


        flash(
            message
        )


    except Exception as exc:

        message = (
            f"FAILED: "
            f"{pdu['name']} / "
            f"Outlet {outlet} / "
            f"{label}: "
            f"{exc}"
        )


        audit(
            f'pdu="{pdu["name"]}" '
            f'ip={ip} '
            f'outlet={outlet} '
            f'asset="{label}" '
            f'action={action_name.upper()} '
            f'result=FAILED '
            f'error="{exc}"'
        )


        flash(
            message
        )


    return redirect(
        url_for("index")
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify(
        {
            "status":
                "ok",

            "backend":
                backend_description(),

            "pdus":
                [
                    pdu["ip"]
                    for pdu in CONFIG["pdus"]
                ],
        }
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
    )
