#!/usr/bin/env bash
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=============================================================="
echo " Installing MIAM PDU Control service"
echo "=============================================================="

apt-get update

apt-get install -y \
    qemu-guest-agent \
    python3 \
    python3-venv \
    python3-pip \
    openssh-client \
    snmp \
    ca-certificates

systemctl enable --now qemu-guest-agent || true

if ! id pducontrol >/dev/null 2>&1; then
    useradd \
        --system \
        --home /opt/pdu-control \
        --shell /usr/sbin/nologin \
        pducontrol
fi

mkdir -p \
    /opt/pdu-control \
    /etc/pdu-control \
    /var/log/pdu-control

install \
    -o pducontrol \
    -g pducontrol \
    -m 600 \
    /root/pdu-control-secrets.env \
    /etc/pdu-control/secrets.env

cat > /etc/pdu-control/config.json <<'CONFIG_EOF'
{
  "pdus": [
    {
      "name": "PDU 1 - Networking",
      "ip": "10.0.20.151",
      "outlets": 24,
      "protected": [],
      "labels": {
        "3": "MIAM-00110 - Dell 7010 Optiplex with KVM",
        "7": "MIAM-00117 - Dell 7010 Optiplex",
        "8": "MIAM-00118 - Dell 7010 Optiplex",
        "9": "MIAM-00119 - Dell 7010 Optiplex"
      }
    },
    {
      "name": "PDU 2 - GPU Servers",
      "ip": "10.0.20.152",
      "outlets": 24,
      "protected": [],
      "labels": {
        "3": "MIAM-00135",
        "4": "MIAM-00114",
        "5": "MIAM-00115",
        "6": "MIAM-00113"
      }
    },
    {
      "name": "PDU 3 - GPU / Infrastructure",
      "ip": "10.0.20.153",
      "outlets": 24,
      "protected": [3, 4, 5, 6, 12],
      "labels": {
        "3": "MIAM-00116 - Firewall",
        "4": "MIAM-00170 - 2.5G Network Switch",
        "5": "MIAM-00171 - 10G Network Switch",
        "6": "MIAM-00120 - 1G Network Switch",
        "7": "MIAM-00147 - NUC 11 Pro",
        "9": "MIAM-00133 - Dell 7010 / Routing",
        "10": "MIAM-00149 - Minisforum Unified Memory",
        "11": "MIAM-00100 - Minisforum MS-01",
        "12": "MIAM-00172 - JetKVM Hardware Console"
      }
    }
  ]
}
CONFIG_EOF

chmod 640 /etc/pdu-control/config.json
chown root:pducontrol /etc/pdu-control/config.json

python3 -m venv /opt/pdu-control/venv

/opt/pdu-control/venv/bin/pip install --upgrade pip

/opt/pdu-control/venv/bin/pip install \
    flask \
    gunicorn \
    pexpect

cat > /opt/pdu-control/app.py <<'APP_EOF'
#!/usr/bin/env python3

import base64
import datetime
import hmac
import json
import os
import re
import subprocess
import time

import pexpect

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template_string,
    request,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.urandom(32)

CONFIG_FILE = "/etc/pdu-control/config.json"
SECRETS_FILE = "/etc/pdu-control/secrets.env"
AUDIT_FILE = "/var/log/pdu-control/audit.log"

# Tripp Lite outlet table.
#
# The controller dynamically obtains the instance suffix from the
# outlet-state table instead of assuming the internal PDU index.
STATE_BASE = "1.3.6.1.4.1.850.1.1.3.2.3.3.1.1.4"
COMMAND_BASE = "1.3.6.1.4.1.850.1.1.3.2.3.3.1.1.6"

ACTION_CODES = {
    "off": 1,
    "on": 2,
    "reboot": 3,
}


def load_secret_file():
    values = {}

    with open(SECRETS_FILE, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if not line or "=" not in line:
                continue

            key, value = line.split("=", 1)
            values[key] = value

    return values


RAW_SECRETS = load_secret_file()


def secret(name):
    encoded = RAW_SECRETS.get(f"{name}_B64", "")

    if not encoded:
        return ""

    try:
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return ""


PDU_USER = secret("PDU_USER")
PDU_PASS = secret("PDU_PASS")
SNMP_VERSION = secret("SNMP_VERSION") or "2c"
SNMP_RO = secret("SNMP_RO")
SNMP_RW = secret("SNMP_RW")
WEB_USER = secret("WEB_USER") or "admin"
WEB_PASS = secret("WEB_PASS")


with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
    CONFIG = json.load(handle)


def backend_description():
    if SNMP_RW and PDU_PASS:
        return "SNMP preferred + SSH fallback"

    if SNMP_RW:
        return "SNMP"

    if PDU_PASS:
        if SNMP_RO:
            return "SNMP status + SSH control"
        return "Legacy SSH control"

    if SNMP_RO:
        return "SNMP status only"

    return "No control credentials configured"


def authentication_failure():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="MIAM PDU Control"'},
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


def audit(message):
    timestamp = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    remote = request.remote_addr if request else "unknown"

    line = (
        f"{timestamp} "
        f"user={WEB_USER} "
        f"source={remote} "
        f"{message}\n"
    )

    with open(AUDIT_FILE, "a", encoding="utf-8") as handle:
        handle.write(line)


def recent_audit(lines=20):
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as handle:
            content = handle.readlines()

        return "".join(content[-lines:])

    except FileNotFoundError:
        return "No actions have been performed yet.\n"


def parse_state_number(text):
    match = re.search(r"\((\d+)\)", text)

    if match:
        return int(match.group(1))

    match = re.search(r"INTEGER:\s*(\d+)", text)

    if match:
        return int(match.group(1))

    return None


def state_name(number):
    if number == 1:
        return "OFF"

    if number == 2:
        return "ON"

    if number is None:
        return "UNKNOWN"

    return str(number)


def snmp_walk_states(ip):
    community = SNMP_RO or SNMP_RW

    if not community:
        return {}

    command = [
        "snmpwalk",
        f"-v{SNMP_VERSION}",
        "-c",
        community,
        "-On",
        "-t",
        "2",
        "-r",
        "1",
        ip,
        STATE_BASE,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0:
        return {}

    states = {}

    prefix = STATE_BASE + "."

    for raw_line in result.stdout.splitlines():
        if "=" not in raw_line:
            continue

        oid_text, value_text = raw_line.split("=", 1)

        oid = oid_text.strip().lstrip(".")

        if not oid.startswith(prefix):
            continue

        suffix = oid[len(prefix):]

        try:
            outlet = int(suffix.split(".")[-1])
        except ValueError:
            continue

        # If a card unexpectedly exposes more than one internal
        # outlet device, retain the first occurrence for a given
        # physical outlet number.
        if outlet in states:
            continue

        number = parse_state_number(value_text)

        states[outlet] = {
            "suffix": suffix,
            "state": state_name(number),
            "raw": value_text.strip(),
        }

    return states


def snmp_action(ip, outlet, action):
    if not SNMP_RW:
        raise RuntimeError(
            "No SNMP read/write community is configured."
        )

    states = snmp_walk_states(ip)

    if outlet not in states:
        raise RuntimeError(
            "Could not discover the SNMP instance for "
            f"outlet {outlet}."
        )

    suffix = states[outlet]["suffix"]
    oid = f"{COMMAND_BASE}.{suffix}"
    code = ACTION_CODES[action]

    command = [
        "snmpset",
        f"-v{SNMP_VERSION}",
        "-c",
        SNMP_RW,
        "-On",
        "-t",
        "3",
        "-r",
        "1",
        ip,
        oid,
        "i",
        str(code),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=12,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "SNMP SET failed."
        )

    return result.stdout.strip()


SSH_PROMPT = r"(?m)^\s*>>\s*$"

CONFIRM_PROMPT = (
    r"(?i)"
    r"(are\s+you\s+sure|"
    r"confirm|"
    r"\by\s*/\s*n\b|"
    r"\byes\s*/\s*no\b)"
)


def ssh_wait_for_login(child):
    for _ in range(8):
        match = child.expect(
            [
                r"(?i)are you sure you want to continue connecting",
                r"(?i)password:",
                r"(?i)press.*enter",
                SSH_PROMPT,
                pexpect.EOF,
                pexpect.TIMEOUT,
            ],
            timeout=12,
        )

        if match == 0:
            child.sendline("yes")
            continue

        if match == 1:
            child.sendline(PDU_PASS)
            continue

        if match == 2:
            child.sendline("")
            continue

        if match == 3:
            return

        if match == 4:
            raise RuntimeError(
                "PDU SSH connection closed during login."
            )

        raise RuntimeError(
            "Timed out waiting for the PDU SSH menu."
        )

    raise RuntimeError(
        "Could not reach the PDU SSH main menu."
    )


def ssh_menu_step(child, value):
    child.sendline(str(value))

    match = child.expect(
        [
            SSH_PROMPT,
            CONFIRM_PROMPT,
            pexpect.EOF,
            pexpect.TIMEOUT,
        ],
        timeout=12,
    )

    output = child.before or ""

    if match == 0:
        return output

    if match == 1:
        child.sendline("y")

        child.expect(
            [
                SSH_PROMPT,
                pexpect.EOF,
                pexpect.TIMEOUT,
            ],
            timeout=12,
        )

        return output + "\nConfirmation sent."

    if match == 2:
        raise RuntimeError(
            "PDU SSH session closed unexpectedly."
        )

    raise RuntimeError(
        "Timed out waiting for the next PDU menu."
    )


def ssh_action(ip, outlet, action):
    if not PDU_USER or not PDU_PASS:
        raise RuntimeError(
            "Legacy SSH credentials are not configured."
        )

    ssh_args = [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
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
        "Ciphers=+aes128-cbc,3des-cbc,aes256-cbc",
        f"{PDU_USER}@{ip}",
    ]

    child = pexpect.spawn(
        "ssh",
        ssh_args,
        encoding="utf-8",
        timeout=15,
    )

    try:
        ssh_wait_for_login(child)

        # Existing PowerAlert menu sequence from the PDU:
        #
        #   1 = Devices
        #   1 = Outlets
        #   N = physical outlet
        #   1/2/3 = Off/On/Reboot
        ssh_menu_step(child, "1")
        ssh_menu_step(child, "1")
        ssh_menu_step(child, str(outlet))

        code = ACTION_CODES[action]
        output = ssh_menu_step(child, str(code))

        child.sendline("Q")

        return output

    finally:
        try:
            child.close(force=True)
        except Exception:
            pass


def perform_action(ip, outlet, action):
    failures = []

    if SNMP_RW:
        try:
            result = snmp_action(
                ip,
                outlet,
                action,
            )

            return "SNMP", result

        except Exception as exc:
            failures.append(
                f"SNMP failed: {exc}"
            )

    if PDU_PASS:
        try:
            result = ssh_action(
                ip,
                outlet,
                action,
            )

            return "SSH", result

        except Exception as exc:
            failures.append(
                f"SSH failed: {exc}"
            )

    if failures:
        raise RuntimeError(
            " | ".join(failures)
        )

    raise RuntimeError(
        "No write-capable PDU control method is configured."
    )


def find_pdu(ip):
    for pdu in CONFIG["pdus"]:
        if pdu["ip"] == ip:
            return pdu

    return None


HTML = r"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MIAM Rack PDU Control</title>
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            font-family:
                Inter,
                Arial,
                sans-serif;
            background: #151622;
            color: #e4e6f1;
            margin: 0;
            padding: 28px;
        }

        .container {
            max-width: 1450px;
            margin: auto;
        }

        h1 {
            font-size: 34px;
            margin-bottom: 5px;
        }

        .subtitle {
            color: #a8adbd;
            margin-bottom: 28px;
        }

        .pdu {
            background: #242635;
            border: 1px solid #35384b;
            border-radius: 12px;
            margin-bottom: 28px;
            overflow: hidden;
        }

        .pdu-header {
            padding: 20px 24px;
            background: #2d3042;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 20px;
        }

        .pdu-title {
            font-size: 23px;
            font-weight: bold;
            color: #88b4ff;
        }

        .ip {
            color: #b8bed0;
            font-family: monospace;
        }

        .outlet {
            display: grid;
            grid-template-columns:
                75px
                minmax(240px, 1fr)
                110px
                380px;
            gap: 12px;
            align-items: center;
            border-top: 1px solid #35384b;
            padding: 11px 22px;
        }

        .outlet:hover {
            background: #292c3d;
        }

        .number {
            font-family: monospace;
            color: #bfc7da;
        }

        .label {
            font-weight: 600;
        }

        .state {
            font-weight: bold;
            font-family: monospace;
        }

        .state.ON {
            color: #91e6a4;
        }

        .state.OFF {
            color: #ff8fa8;
        }

        .state.UNKNOWN {
            color: #e8c980;
        }

        form {
            display: inline-block;
        }

        button {
            border: 0;
            border-radius: 6px;
            padding: 9px 14px;
            margin: 2px;
            font-weight: 700;
            cursor: pointer;
        }

        .on {
            background: #91e6a4;
            color: #11151a;
        }

        .off {
            background: #ff8fa8;
            color: #11151a;
        }

        .reboot {
            background: #f0d183;
            color: #11151a;
        }

        .protected {
            background: #45485b;
            color: #d4d7e4;
            padding: 8px 12px;
            border-radius: 6px;
            font-weight: bold;
        }

        .flash {
            background: #30374c;
            border-left: 5px solid #88b4ff;
            padding: 14px;
            margin-bottom: 20px;
            border-radius: 5px;
        }

        .audit {
            background: #1c1e2a;
            border: 1px solid #35384b;
            border-radius: 10px;
            padding: 20px;
        }

        pre {
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            color: #a8e6a5;
            margin: 0;
            font-size: 13px;
        }

        .mode {
            margin-bottom: 22px;
            display: inline-block;
            background: #303448;
            padding: 9px 13px;
            border-radius: 7px;
        }

        @media (max-width: 900px) {
            .outlet {
                grid-template-columns: 65px 1fr;
            }

            .controls {
                grid-column: 1 / -1;
            }
        }
    </style>
</head>

<body>
<div class="container">

    <h1>🔌 Rack PDU Power Control Center</h1>

    <div class="subtitle">
        Persistent PDU controller hosted in Proxmox
    </div>

    <div class="mode">
        Backend: {{ backend }}
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

            {% for row in pdu.rows %}
                <div class="outlet">

                    <div class="number">
                        Outlet {{ row.number }}
                    </div>

                    <div class="label">
                        {{ row.label }}
                    </div>

                    <div class="state {{ row.state }}">
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
                                        'Turn this outlet OFF?'
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
                                        'Hard power-cycle this outlet?'
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
        <h2>Recent Actions</h2>
        <pre>{{ audit }}</pre>
    </div>

</div>
</body>
</html>
"""


@app.get("/")
def index():
    view_pdus = []

    for pdu in CONFIG["pdus"]:
        states = snmp_walk_states(
            pdu["ip"]
        )

        protected = set(
            int(x)
            for x in pdu.get(
                "protected",
                [],
            )
        )

        labels = pdu.get(
            "labels",
            {},
        )

        rows = []

        for outlet in range(
            1,
            int(pdu.get("outlets", 24)) + 1,
        ):
            info = states.get(
                outlet,
                {},
            )

            rows.append(
                {
                    "number": outlet,
                    "label": labels.get(
                        str(outlet),
                        f"Outlet {outlet}",
                    ),
                    "state": info.get(
                        "state",
                        "UNKNOWN",
                    ),
                    "protected": (
                        outlet in protected
                    ),
                }
            )

        view_pdus.append(
            {
                "name": pdu["name"],
                "ip": pdu["ip"],
                "rows": rows,
            }
        )

    return render_template_string(
        HTML,
        pdus=view_pdus,
        backend=backend_description(),
        audit=recent_audit(),
    )


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
        flash("Invalid outlet number.")
        return redirect(url_for("index"))

    pdu = find_pdu(ip)

    if not pdu:
        flash("Unknown PDU.")
        return redirect(url_for("index"))

    if action_name not in ACTION_CODES:
        flash("Invalid action.")
        return redirect(url_for("index"))

    max_outlets = int(
        pdu.get(
            "outlets",
            24,
        )
    )

    if outlet < 1 or outlet > max_outlets:
        flash("Outlet is outside the configured range.")
        return redirect(url_for("index"))

    protected = set(
        int(x)
        for x in pdu.get(
            "protected",
            [],
        )
    )

    if (
        outlet in protected
        and action_name in ("off", "reboot")
    ):
        message = (
            f"BLOCKED: {pdu['name']} "
            f"outlet {outlet} is protected."
        )

        audit(message)

        flash(message)

        return redirect(url_for("index"))

    label = pdu.get(
        "labels",
        {},
    ).get(
        str(outlet),
        f"Outlet {outlet}",
    )

    try:
        backend, result = perform_action(
            ip,
            outlet,
            action_name,
        )

        audit(
            f'pdu="{pdu["name"]}" '
            f'ip={ip} '
            f'outlet={outlet} '
            f'asset="{label}" '
            f'action={action_name.upper()} '
            f'backend={backend} '
            f'result=SUCCESS'
        )

        flash(
            f"{action_name.upper()} sent successfully: "
            f"{pdu['name']} / "
            f"Outlet {outlet} / "
            f"{label} "
            f"using {backend}."
        )

    except Exception as exc:
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
            f"FAILED: {exc}"
        )

    time.sleep(0.7)

    return redirect(
        url_for("index")
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "backend": backend_description(),
        "pdus": [
            pdu["ip"]
            for pdu in CONFIG["pdus"]
        ],
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
    )
APP_EOF

chmod 750 /opt/pdu-control/app.py

cat > /etc/systemd/system/pdu-control.service <<'SYSTEMD_EOF'
[Unit]
Description=MIAM Rack PDU Control Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple

User=pducontrol
Group=pducontrol

WorkingDirectory=/opt/pdu-control

Environment=PYTHONUNBUFFERED=1

ExecStart=/opt/pdu-control/venv/bin/gunicorn \
    --workers 1 \
    --threads 4 \
    --bind 0.0.0.0:5000 \
    --timeout 45 \
    --access-logfile - \
    --error-logfile - \
    app:app

Restart=always
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

touch /var/log/pdu-control/audit.log

chown -R pducontrol:pducontrol \
    /opt/pdu-control \
    /var/log/pdu-control

chown root:pducontrol \
    /etc/pdu-control/config.json

chmod 640 \
    /etc/pdu-control/config.json

systemctl daemon-reload
systemctl enable pdu-control
systemctl restart pdu-control

sleep 3

echo
echo "=============================================================="
echo " PDU CONTROL SERVICE STATUS"
echo "=============================================================="

systemctl --no-pager --full status pdu-control || true

echo
echo "Listening sockets:"
ss -lntp | grep ':5000' || true

echo
echo "Installation inside VM complete."
