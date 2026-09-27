#!/usr/bin/env python3
"""
MIAM Rack PDU Power Control Center — upgraded per PDU-MANAGER-LDAP-AI-API-UPGRADE-PLAN-V3.

UI: session login (LLDAP) + emergency local administrator fallback.
API: /api/v1 blueprint (HTTP Basic with LLDAP credentials).
Power operations: ONE shared path via action_service (central protection
invariant, idempotency, audit, per-PDU lock, native PDU cycle).
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)

import app_runtime as rt
import action_service
from action_service import ActionError
import auth_lldap
from auth_lldap import (
    Actor,
    authenticate_lldap,
    verify_credentials,
    LdapUnavailable,
)
from api_v1 import api_v1

COMMAND_TIMEOUT_SECONDS = rt.COMMAND_TIMEOUT_SECONDS
MAX_BATCH_ITEMS = rt.MAX_BATCH_ITEMS
AUDIT_FILE = rt.AUDIT_FILE
CONFIG_FILE = rt.CONFIG_FILE

# Emergency local administrator = the pre-upgrade root Basic credentials,
# preserved for the emergency fallback path only (plan section 1.3/21).
EMERGENCY_USER = rt.secret("WEB_USER") or "root"
EMERGENCY_PASS = rt.secret("WEB_PASS")
if not EMERGENCY_PASS:
    raise RuntimeError("Emergency dashboard password is not configured.")

app = Flask(__name__)
# Stable signing key so sessions survive service restarts; not derived from the
# emergency password alone. Rotating EMERGENCY_PASS rotates the key as well.
app.secret_key = hashlib.sha256(
    f"pdu-control-v3:{EMERGENCY_USER}:{EMERGENCY_PASS}".encode("utf-8")
).digest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
)
if os.environ.get("PDU_SECURE_COOKIES", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True

app.register_blueprint(api_v1)

rt.startup_tasks()


# ----------------------------------------------------------------------------
# Session helpers
# ----------------------------------------------------------------------------

def load_actor_from_session():
    username = session.get("u")
    if not username:
        return None
    auth_source = session.get("src", "lldap")
    if auth_source == "emergency-local":
        return auth_lldap.emergency_actor()
    groups = session.get("g", [])
    return Actor(username, groups, "lldap")


def store_actor_session(actor):
    session.clear()
    session["u"] = actor.username
    session["src"] = actor.auth_source
    session["g"] = list(actor.groups)
    session.permanent = True


ROLE_LABELS = {
    "admin": "pdu-admin",
    "operator": "pdu-operator",
    "viewer": "pdu-viewer",
    "ai": "pdu-ai-agent",
    "none": "no PDU group",
}


def role_label(actor):
    if actor.auth_source == "emergency-local":
        return "administrator (local)"
    if actor.can_administer_manager():
        return "administrator"
    if actor.can_control_normal():
        return "operator"
    if actor.can_view():
        return "viewer"
    return "no PDU group"


@app.before_request
def gate():
    """Session gate for the web UI; /api/v1 authenticates itself (Basic+LLDAP)."""
    if request.path.startswith("/api/v1"):
        return None
    if request.path in ("/login", "/health"):
        return None

    actor = load_actor_from_session()
    if actor is None and request.authorization:
        # Transitional Basic-auth fallback for legacy scripts / CLI checks:
        # emergency root first, then any valid LLDAP account with a PDU group.
        auth = request.authorization
        if (hmac.compare_digest(auth.username or "", EMERGENCY_USER)
                and hmac.compare_digest(auth.password or "", EMERGENCY_PASS)):
            actor = auth_lldap.emergency_actor()
        else:
            try:
                candidate = authenticate_lldap(auth.username or "", auth.password or "")
            except LdapUnavailable:
                candidate = None
            if candidate and candidate.can_view():
                actor = candidate

    if actor is None:
        if request.path.startswith("/api/") or request.path.startswith("/audit"):
            return jsonify({"accepted": False, "error": "Authentication required."}), 401
        return redirect("/login")

    g.actor = actor
    return None


# ----------------------------------------------------------------------------
# Login / logout
# ----------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        mode = request.form.get("mode", "lldap")
        if mode == "emergency":
            e_user = request.form.get("e_user", "")
            e_pass = request.form.get("e_pass", "")
            if (hmac.compare_digest(e_user, EMERGENCY_USER)
                    and hmac.compare_digest(e_pass, EMERGENCY_PASS)):
                store_actor_session(auth_lldap.emergency_actor())
                rt.audit("login=SUCCESS auth_source=emergency-local", "web",
                         actor=EMERGENCY_USER,
                         extra={"ip": request.remote_addr or "unknown"})
                return redirect("/")
            error = "Emergency credentials rejected."
            rt.audit("login=FAILED auth_source=emergency-local", "web",
                     actor=e_user or "unknown",
                     extra={"ip": request.remote_addr or "unknown"})
        else:
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            try:
                actor = authenticate_lldap(username, password, force_refresh=True)
            except LdapUnavailable as exc:
                actor = None
                error = f"LLDAP unreachable ({exc}). Use the emergency path if the directory is down."
            if actor is not None and not error:
                if not actor.can_view():
                    error = "Account authenticated but has no PDU access group."
                    rt.audit(f"login=REJECTED reason=no_pdu_group user={username}", "web",
                             actor=username,
                             extra={"ip": request.remote_addr or "unknown"})
                else:
                    store_actor_session(actor)
                    rt.audit(
                        f"login=SUCCESS auth_source=lldap groups={','.join(actor.groups)}",
                        "web", actor=username,
                        extra={"ip": request.remote_addr or "unknown"})
                    return redirect("/")
            elif actor is None and not error:
                error = "Invalid username or password."
                rt.audit(f"login=FAILED auth_source=lldap", "web", actor=username or "unknown",
                         extra={"ip": request.remote_addr or "unknown"})
    return render_template_string(LOGIN_HTML, error=error)


@app.get("/logout")
def logout():
    who = session.get("u") or "unknown"
    session.clear()
    rt.audit("logout", "web", actor=who)
    return redirect("/login")


# ----------------------------------------------------------------------------
# Validation helpers (thin wrappers over action_service)
# ----------------------------------------------------------------------------

def validate_action(action):
    try:
        return action_service.normalize_action(action)
    except ActionError as exc:
        raise ValueError(exc.message)


def validate_item(ip, outlet, action):
    """Legacy signature kept for the Markdown config path (validation only)."""
    try:
        target = action_service.validate_target(ip, outlet, action)
    except ActionError as exc:
        raise ValueError(exc.message)
    return {
        "ip": target["ip"],
        "outlet": target["outlet"],
        "label": target["label"],
        "pdu_name": target["pdu_name"],
        "asset_id": target["asset_id"],
    }


# ----------------------------------------------------------------------------
# Markdown config (export/upload) — preserved behavior
# ----------------------------------------------------------------------------

def build_markdown(action, items):
    generated = rt.utc_iso()
    lines = [
        "# MIAM PDU Master Control Configuration",
        "",
        "This Markdown file can be uploaded back into the PDU dashboard to restore a batch selection.",
        "Uploading does **not** execute the commands. Review the selection and press **SEND TO SELECTED** in the web UI.",
        "",
        f"- Schema version: 1",
        f"- Generated UTC: {generated}",
        f"- Batch action: {action.upper()}",
        "",
        "## Machine-readable batch block",
        "",
        "Do not remove these comments if you want to upload this file again.",
        "",
        f"<!-- PDU_BATCH_ACTION={action} -->",
    ]

    for order, item in enumerate(items, start=1):
        lines.append(
            f"<!-- PDU_BATCH_ITEM={order}|{item['ip']}|{item['outlet']} -->"
        )

    lines.extend(
        [
            "",
            "## Selected command sequence",
            "",
            "| Order | PDU ID | IP | Outlet | Label |",
            "| ---: | --- | --- | ---: | --- |",
        ]
    )

    if items:
        for order, item in enumerate(items, start=1):
            pdu = rt.find_pdu(item["ip"])
            label = rt.label_for(pdu, item["outlet"]).replace("|", "/")
            lines.append(
                f"| {order} | {pdu.get('asset_id', '')} | {item['ip']} | {item['outlet']} | {label} |"
            )
    else:
        lines.append("| - | - | - | - | No outlets selected |")

    lines.extend(["", "## PDU inventory", ""])
    for pdu in rt.CONFIG["pdus"]:
        lines.extend(
            [
                f"### {pdu.get('asset_id', '')} - {pdu['name']}",
                "",
                f"- IP: {pdu['ip']}",
                f"- Protected OFF/REBOOT outlets: {', '.join(str(v) for v in pdu.get('protected', [])) or 'None'}",
                "",
                "| Outlet | Label |",
                "| ---: | --- |",
            ]
        )
        for outlet in range(1, int(pdu.get("outlets", 24)) + 1):
            label = rt.label_for(pdu, outlet).replace("|", "/")
            lines.append(f"| {outlet} | {label} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def parse_markdown_config(text):
    action_match = re.search(
        r"<!--\s*PDU_BATCH_ACTION=(on|off|reboot)\s*-->",
        text,
        flags=re.IGNORECASE,
    )
    if not action_match:
        action_match = re.search(
            r"(?im)^\s*-?\s*Batch action:\s*(ON|OFF|REBOOT)\s*$",
            text,
        )
    if not action_match:
        raise ValueError("No valid Batch action was found in the Markdown file.")

    action = action_match.group(1).lower()
    item_matches = re.findall(
        r"<!--\s*PDU_BATCH_ITEM=(\d+)\|([0-9.]+)\|(\d+)\s*-->",
        text,
        flags=re.IGNORECASE,
    )
    if not item_matches:
        raise ValueError("No machine-readable PDU_BATCH_ITEM entries were found.")

    ordered = []
    for order_text, ip, outlet_text in item_matches:
        order = int(order_text)
        item = validate_item(ip, int(outlet_text), "on")
        ordered.append((order, {"ip": item["ip"], "outlet": item["outlet"]}))

    ordered.sort(key=lambda pair: pair[0])
    items = [item for _, item in ordered]
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"Configuration contains more than {MAX_BATCH_ITEMS} selected outlets.")

    return action, items


HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MIAM Rack PDU Control</title>
<style>
:root {
    --toolbar-offset: 86px;
    --sticky-gap: 6px;
    --row-on: #242635;
    --row-off: #363840;
    --row-unknown: #2a2b37;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: calc(var(--toolbar-offset) + 100px); }
body {
    margin: 0;
    padding: 0 28px 32px 28px;
    background: #151622;
    color: #e8eaf3;
    font-family: Inter, Arial, Helvetica, sans-serif;
}
body.drag-selecting { user-select: none; }
.container { max-width: 1550px; margin: auto; }
h1 { margin: 22px 0 4px 0; font-size: 32px; }
.subtitle { color: #aeb3c4; margin-bottom: 14px; }
.global-toolbar {
    position: sticky;
    top: 0;
    z-index: 400;
    padding: 10px 12px 16px 12px;
    margin: 0 -4px 18px -4px;
    background: rgba(21, 22, 34, 0.985);
    border-bottom: 1px solid #3b4057;
    border-radius: 0 0 10px 10px;
    backdrop-filter: blur(8px);
    box-shadow: 0 6px 18px rgba(0,0,0,.18);
}
.toolbar-topline,
.toolbar-actions {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-wrap: wrap;
    padding-right: 34px;
}
.toolbar-actions { margin-top: 8px; }
.toolbar-actions.hidden { display: none; }
.mode, .selection-count {
    border-radius: 8px;
    padding: 8px 11px;
    font-size: 12px;
    background: #222536;
    border: 1px solid #3a3f55;
    color: #9fd0ff;
}
.selection-count { color: #dbe4ff; font-weight: 800; }
.toolbar-spacer { flex: 1; }
.toolbar-button, .refresh, select {
    border: 1px solid #4c526b;
    border-radius: 7px;
    padding: 8px 11px;
    background: #3a4057;
    color: #fff;
    font-size: 12px;
    font-weight: 800;
    text-decoration: none;
    cursor: pointer;
}
.toolbar-button.primary { background: #6478d8; }
.toolbar-button.save { background: #456b5c; }
.toolbar-button.upload { background: #5b536f; }
.toolbar-button.preset { background: #4b526b; }
.toolbar-button.select-all { background: #435c77; }
.toolbar-button:hover, .refresh:hover { filter: brightness(1.08); }
.toolbar-toggle {
    position: absolute;
    right: 7px;
    bottom: 3px;
    width: 27px;
    height: 23px;
    padding: 0;
    border: 1px solid #4c526b;
    border-radius: 6px;
    background: #292d3f;
    color: #dbe4ff;
    font-size: 15px;
    line-height: 20px;
    font-weight: 900;
    cursor: pointer;
}
.preset-panel {
    display: none;
    gap: 7px;
    flex-wrap: wrap;
    margin-top: 9px;
    padding: 9px 36px 2px 0;
    border-top: 1px dashed #41465d;
}
.preset-panel.open { display: flex; }
.preset-chip {
    border: 1px solid #545c78;
    border-radius: 999px;
    padding: 7px 10px;
    background: #292e42;
    color: #dce4f7;
    font-size: 11px;
    font-weight: 800;
    cursor: pointer;
}
.preset-chip:hover { background: #343a52; }
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
    overflow: visible;
}
.pdu-header {
    position: sticky;
    top: calc(var(--toolbar-offset) + var(--sticky-gap));
    z-index: 250;
    min-height: 70px;
    display: grid;
    grid-template-columns: minmax(300px, 1fr) auto minmax(255px, auto);
    gap: 16px;
    align-items: center;
    padding: 10px 18px;
    background: rgba(48, 51, 72, 0.985);
    border-radius: 11px 11px 0 0;
    border-bottom: 1px solid #41455c;
    backdrop-filter: blur(8px);
    box-shadow: 0 5px 14px rgba(0,0,0,.20);
}
.pdu-title { font-size: 17px; font-weight: 800; color: #7eb2ff; }
.pdu-meta { color: #c2c8dc; font-family: monospace; font-size: 12px; margin-top: 4px; }
.pdu-status {
    min-width: 255px;
    padding: 9px 12px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 900;
    text-align: center;
    background: #263b31;
    color: #8cf0ad;
    border: 1px solid #3c6750;
}
.pdu-status.busy {
    background: #493e24;
    color: #ffd66e;
    border-color: #766128;
}
.pdu-status.failed {
    background: #4d2d35;
    color: #ffb3c0;
    border-color: #7e4250;
}
.pdu-error { padding: 10px 18px; background: #4d2d35; color: #ffd1d9; font-size: 13px; }
.outlet {
    min-height: 62px;
    display: grid;
    grid-template-columns: 46px 78px minmax(280px, 1fr) 78px 300px 250px;
    align-items: center;
    gap: 8px;
    padding: 7px 16px;
    border-top: 1px solid #343748;
    background: var(--row-on);
    transition: background-color .16s ease, box-shadow .16s ease;
}
.outlet.power-on { background: var(--row-on); }
.outlet.power-off {
    background: var(--row-off);
    box-shadow: inset 4px 0 0 #777b88;
}
.outlet.power-unknown { background: var(--row-unknown); }
.outlet.power-on:hover { background: #292c3d; }
.outlet.power-off:hover { background: #3d3f48; }
.outlet.power-unknown:hover { background: #30323f; }
.outlet.is-selected { outline: 1px solid rgba(126,178,255,.55); outline-offset: -1px; }
.select-cell { display: flex; align-items: center; gap: 4px; min-width: 0; }
.select-cell input { width: 18px; height: 18px; cursor: pointer; flex: 0 0 auto; }
.drag-select-handle {
    display: none;
    width: 18px;
    height: 24px;
    align-items: center;
    justify-content: center;
    border-radius: 5px;
    color: #9fb2db;
    font-size: 16px;
    font-weight: 900;
    cursor: grab;
    touch-action: none;
    user-select: none;
}
.drag-select-handle.active { background: #485171; color: #fff; }
.order-badge {
    min-width: 18px;
    text-align: center;
    font-size: 10px;
    color: #9fd0ff;
}
.number { font-family: monospace; font-size: 12px; color: #b8c9eb; }
.label { font-weight: 700; padding-right: 8px; font-size: 13px; min-width: 0; }
.state { font-family: monospace; font-weight: 900; font-size: 13px; }
.state.ON { color: #7ee8a1; }
.state.OFF { color: #ff8297; }
.state.UNKNOWN { color: #f0c45e; }
.controls form { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
button.action-button {
    border: 0;
    border-radius: 6px;
    padding: 9px 13px;
    font-size: 12px;
    font-weight: 900;
    cursor: pointer;
}
button.action-button:disabled { opacity: 0.35; cursor: not-allowed; }
button.on { background: #81dfa0; color: #111827; }
button.off { background: #fb839b; color: #111827; }
button.reboot { background: #f4ce73; color: #111827; }
.protected { color: #ffb3c0; font-size: 11px; font-weight: 800; }
.userbar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin: 0 0 14px 0;
    padding: 9px 12px;
    border-radius: 8px;
    background: #222536;
    border: 1px solid #3a3f55;
    font-size: 13px;
    color: #dbe4ff;
}
.role-chip {
    border-radius: 999px;
    padding: 2px 9px;
    font-size: 10px;
    font-weight: 900;
    background: #2c3550;
    color: #9fd0ff;
}
.role-chip.emergency { background: #4d2d35; color: #ffb3c0; }
button.override { background: #b98fe0; color: #1c1230; }

.cmd-indicator {
    min-height: 34px;
    font-family: monospace;
    font-size: 11px;
    color: #aeb3c4;
    line-height: 1.4;
}
.cmd-indicator:not(:empty) {
    align-self: center;
    padding: 4px 6px;
    border-radius: 5px;
    background: rgba(13, 14, 22, .42);
}
.cmd-indicator.waiting { color: #ffd66e; font-weight: 800; }
.cmd-indicator.success { color: #7ee8a1; font-weight: 800; }
.cmd-indicator.failed { color: #ff8297; font-weight: 800; }
.note { color: #aeb3c4; font-size: 12px; margin-bottom: 17px; line-height: 1.45; }
.audit {
    margin-top: 35px;
    padding: 20px;
    background: #1d1f2c;
    border: 1px solid #343748;
    border-radius: 10px;
}
.audit-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.audit h2 { margin: 0 0 12px 0; }
.audit pre { overflow-x: auto; white-space: pre-wrap; color: #c7ccda; font-size: 12px; }
#toast {
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 1200;
    display: none;
    max-width: min(520px, calc(100vw - 30px));
    padding: 14px 16px;
    border-radius: 8px;
    background: #30354a;
    border: 1px solid #59617b;
    box-shadow: 0 8px 25px rgba(0,0,0,.35);
    white-space: pre-wrap;
}
@media (pointer: coarse) {
    .drag-select-handle { display: inline-flex; }
}
@media (max-width: 1100px) {
    .outlet { grid-template-columns: 46px 70px minmax(0, 1fr) 68px; }
    .controls, .cmd-indicator { grid-column: 3 / -1; }
    .pdu-header { grid-template-columns: 1fr; gap: 6px; }
    .pdu-status { min-width: 0; text-align: left; }
    .pdu-count { display: none; }
}
@media (max-width: 700px) {
    body { padding: 0 8px 24px 8px; }
    h1 { margin-top: 14px; font-size: 24px; line-height: 1.1; }
    .subtitle { font-size: 13px; margin-bottom: 10px; }
    .global-toolbar { margin: 0 -2px 12px -2px; padding: 8px 9px 15px 9px; }
    .toolbar-topline { gap: 6px; }
    .mode { flex: 1 1 100%; padding: 7px 9px; }
    .selection-count { padding: 7px 9px; }
    .toolbar-actions {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
        width: 100%;
        margin-top: 7px;
    }
    .toolbar-actions.hidden { display: none; }
    #batch-action, .toolbar-button.primary { grid-column: 1 / -1; width: 100%; }
    .toolbar-button, .refresh, select {
        min-height: 38px;
        padding: 8px 7px;
        font-size: 11px;
        text-align: center;
    }
    .toolbar-spacer { display: none; }
    .preset-panel.open {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
        padding-right: 30px;
    }
    .preset-chip { border-radius: 7px; min-height: 38px; }
    .pdu { border-radius: 9px; margin-bottom: 20px; }
    .pdu-header {
        min-height: 0;
        padding: 9px 10px;
        border-radius: 8px 8px 0 0;
    }
    .pdu-title { font-size: 14px; line-height: 1.25; }
    .pdu-meta { font-size: 10px; }
    .pdu-status { padding: 7px 9px; font-size: 10px; }
    .outlet {
        grid-template-columns: 46px 60px minmax(0, 1fr) 50px;
        gap: 5px;
        min-height: 82px;
        padding: 8px 8px;
    }
    .number { font-size: 10px; }
    .label { font-size: 12px; line-height: 1.25; overflow-wrap: anywhere; }
    .state { font-size: 11px; text-align: right; }
    .controls, .cmd-indicator { grid-column: 3 / 5; }
    .controls form { gap: 5px; }
    button.action-button { padding: 8px 10px; font-size: 11px; min-height: 36px; }
    .cmd-indicator { min-height: 24px; font-size: 9px; }
    .note { font-size: 10px; }
    .audit { padding: 14px; }
    #toast { right: 10px; bottom: 10px; }
}
@media (max-width: 420px) {
    .preset-panel.open { grid-template-columns: 1fr; }
    .outlet { grid-template-columns: 44px 54px minmax(0, 1fr) 44px; }
    button.action-button { padding: 8px 8px; }
}
</style>
</head>
<body>
<div class="container">
    <h1>Rack PDU Power Control Center</h1>
    <div class="subtitle">Persistent PDU controller hosted in Proxmox VM 154</div>
    <div class="userbar">
        Logged in as <b>{{ username }}</b> <span class="role-chip">{{ role_label }}</span>
        {% if auth_source == 'emergency-local' %}<span class="role-chip emergency">EMERGENCY LOCAL</span>{% endif %}
        &nbsp;|&nbsp; <a class="refresh" href="/logout">LOGOUT</a>
        &nbsp;|&nbsp; <a class="refresh" href="/api/v1/health" target="_blank" rel="noopener">API v1</a>
    </div>

    <div class="global-toolbar" id="global-toolbar">
        <div class="toolbar-topline">
            <div class="mode">Backend: {{ backend }}</div>
            <div class="selection-count" id="selection-count">0 selected</div>
        </div>

        <div class="toolbar-actions" id="toolbar-actions">
            <select id="batch-action" title="Command to send to selected outlets">
                <option value="on">POWER ON selected</option>
                <option value="off">POWER OFF selected</option>
                <option value="reboot">CYCLE / REBOOT selected</option>
            </select>
            <button class="toolbar-button primary" type="button" onclick="sendSelected()">SEND TO SELECTED</button>
            <button class="toolbar-button select-all" type="button" onclick="selectAllOutlets()">SELECT ALL</button>
            <button class="toolbar-button" type="button" onclick="clearSelection()">CLEAR SELECTED</button>
            <button class="toolbar-button preset" id="preset-button" type="button" onclick="togglePresets()">PRESETS</button>
            <button class="toolbar-button save" type="button" onclick="saveConfiguration()">SAVE CONFIG (.md)</button>
            <button class="toolbar-button upload" type="button" onclick="document.getElementById('config-upload').click()">UPLOAD CONFIG (.md)</button>
            <input id="config-upload" type="file" accept=".md,text/markdown,text/plain" hidden onchange="uploadConfiguration(this)">
            <div class="toolbar-spacer"></div>
            <a class="refresh" href="/">REFRESH STATES</a>
        </div>

        <div class="preset-panel" id="preset-panel">
            <button class="preset-chip" type="button" onclick="applyPreset('nonprotected')">All non-protected devices</button>
            <button class="preset-chip" type="button" onclick="applyPreset('notondevices')">All devices currently off</button>
            <button class="preset-chip" type="button" onclick="applyPreset('empty')">All empty ports</button>
            <button class="preset-chip" type="button" onclick="applyPreset('servers1234')">All Servers 1,2,3,4</button>
            <button class="preset-chip" type="button" onclick="applyPreset('dell7010')">All Dell 7010s</button>
            <button class="preset-chip" type="button" onclick="applyPreset('nucdas')">NUC 11 Pro and DAS</button>
            <button class="preset-chip" type="button" onclick="applyPreset('on')">All currently ON</button>
            <button class="preset-chip" type="button" onclick="applyPreset('MIAM-00151')">All MIAM-00151 ports</button>
            <button class="preset-chip" type="button" onclick="applyPreset('MIAM-00152')">All MIAM-00152 ports</button>
            <button class="preset-chip" type="button" onclick="applyPreset('MIAM-00153')">All MIAM-00153 ports</button>
        </div>

        <button
            class="toolbar-toggle"
            id="toolbar-toggle"
            type="button"
            title="Hide action controls"
            aria-label="Hide action controls"
            aria-expanded="true"
            onclick="toggleToolbar()"
        >^</button>
    </div>

    <div class="note">
        Commands are asynchronous. Each PDU is independently locked while processing and each outlet command has a hard 60-second maximum.
        Desktop: hold <b>Shift</b> while clicking checkboxes to select a range. Phone/tablet: long-press the small drag handle beside a checkbox, then drag over outlets to paint-select or paint-deselect.
    </div>

    {% with messages = get_flashed_messages() %}
        {% if messages %}
            {% for message in messages %}<div class="flash">{{ message }}</div>{% endfor %}
        {% endif %}
    {% endwith %}

    {% for pdu in pdus %}
    <div class="pdu" id="pdu-card-{{ pdu.ip|replace('.', '-') }}">
        <div class="pdu-header">
            <div>
                <div class="pdu-title">{{ pdu.name }}</div>
                <div class="pdu-meta">{{ pdu.asset_id }} &nbsp; | &nbsp; {{ pdu.ip }}</div>
            </div>
            <div class="pdu-meta pdu-count">{{ pdu.rows|length }} outlets</div>
            <div class="pdu-status" id="pdu-status-{{ pdu.ip|replace('.', '-') }}">READY</div>
        </div>

        {% if pdu.error %}<div class="pdu-error">STATE READ ERROR: {{ pdu.error }}</div>{% endif %}

        {% for row in pdu.rows %}
        <div
            class="outlet power-{{ row.state|lower }}"
            id="row-{{ pdu.ip|replace('.', '-') }}-{{ row.number }}"
            data-ip="{{ pdu.ip }}"
            data-outlet="{{ row.number }}"
        >
            <div class="select-cell">
                <input
                    type="checkbox"
                    class="outlet-select"
                    data-ip="{{ pdu.ip }}"
                    data-outlet="{{ row.number }}"
                    data-label="{{ row.label|e }}"
                    data-state="{{ row.state }}"
                    data-protected="{{ 'true' if row.protected else 'false' }}"
                    data-asset-id="{{ pdu.asset_id }}"
                    onclick="selectionClicked(event, this)"
                >
                <span class="drag-select-handle" title="Hold, then drag to select multiple" aria-hidden="true">⋮</span>
                <span class="order-badge"></span>
            </div>
            <div class="number">Outlet {{ row.number }}</div>
            <div class="label">{{ row.label }}</div>
            <div class="state {{ row.state }}" id="state-{{ pdu.ip|replace('.', '-') }}-{{ row.number }}">{{ row.state }}</div>
            <div class="controls">
                <form onsubmit="return false;">
                    <button class="action-button on pdu-action" type="button" data-ip="{{ pdu.ip }}" onclick="sendSingle('{{ pdu.ip }}', {{ row.number }}, 'on')">ON</button>
                    {% if not row.protected %}
                    <button class="action-button off pdu-action" type="button" data-ip="{{ pdu.ip }}" onclick="sendSingle('{{ pdu.ip }}', {{ row.number }}, 'off')">OFF</button>
                    <button class="action-button reboot pdu-action" type="button" data-ip="{{ pdu.ip }}" onclick="sendSingle('{{ pdu.ip }}', {{ row.number }}, 'reboot')">REBOOT</button>
                    {% else %}
                    <span class="protected">OFF PROTECTED</span>
                    {% if can_override %}
                    <button class="action-button override pdu-action" type="button" data-ip="{{ pdu.ip }}" onclick="sendOverride('{{ pdu.ip }}', {{ row.number }})">REBOOT (OVERRIDE)</button>
                    {% endif %}
                    {% endif %}
                </form>
            </div>
            <div class="cmd-indicator" id="cmd-{{ pdu.ip|replace('.', '-') }}-{{ row.number }}"></div>
        </div>
        {% endfor %}
    </div>
    {% endfor %}

    <div class="audit">
        <div class="audit-header">
            <h2>Recent Actions</h2>
            <a class="refresh" href="/audit/download">DOWNLOAD FULL LOG</a>
        </div>
        <pre>{{ audit }}</pre>
    </div>
</div>
<div id="toast"></div>

<script>
const selectedSequence = [];
let lastBusy = {};
let lastSelectionBox = null;

function keyFor(ip, outlet) { return `${ip}|${outlet}`; }
function safeIp(ip) { return ip.replaceAll('.', '-'); }
function rowIndicator(ip, outlet) { return document.getElementById(`cmd-${safeIp(ip)}-${outlet}`); }
function allSelectionBoxes() { return Array.from(document.querySelectorAll('.outlet-select')); }

function showToast(message, ms=6000) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.display = 'block';
    clearTimeout(window.__toastTimer);
    window.__toastTimer = setTimeout(() => { toast.style.display = 'none'; }, ms);
}

function itemFromBox(box) {
    return {
        ip: box.dataset.ip,
        outlet: Number(box.dataset.outlet),
        label: box.dataset.label
    };
}

function setBoxSelected(box, desired) {
    const key = keyFor(box.dataset.ip, box.dataset.outlet);
    const existing = selectedSequence.findIndex(item => keyFor(item.ip, item.outlet) === key);
    box.checked = Boolean(desired);

    if (desired && existing < 0) {
        selectedSequence.push(itemFromBox(box));
    } else if (!desired && existing >= 0) {
        selectedSequence.splice(existing, 1);
    }
}

function selectionClicked(event, box) {
    const boxes = allSelectionBoxes();
    const currentIndex = boxes.indexOf(box);
    const desired = box.checked;

    if (event.shiftKey && lastSelectionBox) {
        const previousIndex = boxes.indexOf(lastSelectionBox);
        if (previousIndex >= 0 && currentIndex >= 0) {
            const step = previousIndex <= currentIndex ? 1 : -1;
            for (let i = previousIndex; ; i += step) {
                setBoxSelected(boxes[i], desired);
                if (i === currentIndex) break;
            }
        } else {
            setBoxSelected(box, desired);
        }
    } else {
        setBoxSelected(box, desired);
    }

    lastSelectionBox = box;
    renderSelectionOrder();
}

function renderSelectionOrder() {
    allSelectionBoxes().forEach(box => {
        const index = selectedSequence.findIndex(item => keyFor(item.ip, item.outlet) === keyFor(box.dataset.ip, box.dataset.outlet));
        box.checked = index >= 0;
        box.parentElement.querySelector('.order-badge').textContent = index >= 0 ? `#${index + 1}` : '';
        const row = box.closest('.outlet');
        if (row) row.classList.toggle('is-selected', index >= 0);
    });
    document.getElementById('selection-count').textContent = `${selectedSequence.length} selected`;
}

function clearSelection() {
    selectedSequence.splice(0, selectedSequence.length);
    lastSelectionBox = null;
    renderSelectionOrder();
}

function selectAllOutlets() {
    selectedSequence.splice(0, selectedSequence.length);
    allSelectionBoxes().forEach(box => selectedSequence.push(itemFromBox(box)));
    lastSelectionBox = null;
    renderSelectionOrder();
    showToast(`Selected all ${selectedSequence.length} outlets.`);
}

function isEmptyPort(box) {
    return String(box.dataset.label || '').trim().toLowerCase() === `outlet ${box.dataset.outlet}`.toLowerCase();
}

function applyPreset(name) {
    let matcher;
    let title = name;

    if (name === 'nonprotected') {
        title = 'All non-protected devices';
        matcher = box => !isEmptyPort(box) && box.dataset.protected !== 'true';
    } else if (name === 'notondevices') {
        title = 'All devices currently off';
        // Deliberately follows the requested rule: select named devices whose
        // current status is anything other than ON (for example OFF or UNKNOWN).
        matcher = box => !isEmptyPort(box) && String(box.dataset.state || '').toUpperCase() !== 'ON';
    } else if (name === 'empty') {
        title = 'All empty ports';
        matcher = box => isEmptyPort(box);
    } else if (name === 'servers1234') {
        title = 'All Servers 1,2,3,4';
        const serverIds = /MIAM-00111|MIAM-00112|MIAM-00143|MIAM-00144/i;
        matcher = box => serverIds.test(box.dataset.label || '');
    } else if (name === 'dell7010') {
        title = 'All Dell 7010s';
        matcher = box => /dell\s*7010/i.test(box.dataset.label || '');
    } else if (name === 'nucdas') {
        title = 'NUC 11 Pro and DAS';
        matcher = box => /NUC\s*11|NUC11|ThunderBay|\bDAS\b|Docking Station/i.test(box.dataset.label || '');
    } else if (name === 'on') {
        title = 'All currently ON';
        matcher = box => String(box.dataset.state || '').toUpperCase() === 'ON';
    } else if (/^MIAM-0015[123]$/.test(name)) {
        title = `All ${name} ports`;
        matcher = box => box.dataset.assetId === name;
    } else {
        showToast(`Unknown preset: ${name}`);
        return;
    }

    selectedSequence.splice(0, selectedSequence.length);
    allSelectionBoxes().filter(matcher).forEach(box => selectedSequence.push(itemFromBox(box)));
    lastSelectionBox = null;
    renderSelectionOrder();
    showToast(`${title}: selected ${selectedSequence.length} outlet(s). Review before sending a command.`, 7000);
}

function togglePresets() {
    const panel = document.getElementById('preset-panel');
    panel.classList.toggle('open');
    updateStickyOffset();
}

function setToolbarCollapsed(collapsed, persist=true) {
    const actions = document.getElementById('toolbar-actions');
    const presets = document.getElementById('preset-panel');
    const toggle = document.getElementById('toolbar-toggle');

    actions.classList.toggle('hidden', collapsed);
    if (collapsed) presets.classList.remove('open');
    toggle.textContent = collapsed ? 'v' : '^';
    toggle.title = collapsed ? 'Show action controls' : 'Hide action controls';
    toggle.setAttribute('aria-label', toggle.title);
    toggle.setAttribute('aria-expanded', String(!collapsed));

    if (persist) {
        try { localStorage.setItem('pduToolbarCollapsed', collapsed ? '1' : '0'); } catch (error) {}
    }

    requestAnimationFrame(updateStickyOffset);
}

function toggleToolbar() {
    const actions = document.getElementById('toolbar-actions');
    setToolbarCollapsed(!actions.classList.contains('hidden'));
}

function updateStickyOffset() {
    const toolbar = document.getElementById('global-toolbar');
    if (!toolbar) return;
    const height = Math.ceil(toolbar.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--toolbar-offset', `${height}px`);
}

function setPduButtons(ip, disabled) {
    document.querySelectorAll(`.pdu-action[data-ip="${ip}"]`).forEach(button => { button.disabled = disabled; });
}

async function sendSingle(ip, outlet, action) {
    const pretty = action === 'reboot' ? 'CYCLE / REBOOT' : action.toUpperCase();
    if (!confirm(`${pretty} ${ip} / Outlet ${outlet}?`)) return;

    const indicator = rowIndicator(ip, outlet);
    indicator.className = 'cmd-indicator waiting';
    indicator.textContent = `SENDING ${pretty}...`;
    setPduButtons(ip, true);

    try {
        const response = await fetch('/api/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ip, outlet, action})
        });
        const data = await response.json();
        const errMsg = (data.error && data.error.message) ? data.error.message : data.error;
        if (!response.ok) throw new Error(errMsg || 'Command was rejected.');
        indicator.textContent = `SENT ${pretty} - WAITING 0.0s`;
        showToast(`Command sent to ${ip} outlet ${outlet}. Please wait for command to be processed.`);
        await pollStatus();
    } catch (error) {
        indicator.className = 'cmd-indicator failed';
        indicator.textContent = `NOT SENT: ${error.message}`;
        setPduButtons(ip, false);
        showToast(error.message);
    }
}

async function sendOverride(ip, outlet) {
    const isSelfHost = (ip === '10.0.20.153' && outlet === 9);
    let warning = `ADMINISTRATIVE OVERRIDE REBOOT\n\n${ip} / Outlet ${outlet}\n\nThis protected outlet will be power-cycled with the PDU's native Cycle-Load operation. It must return to ON.`;
    if (isSelfHost) {
        warning += `\n\nWARNING: This outlet powers MIAM-00133, which hosts this VM154/PDU Manager. The controller may become temporarily unreachable; the PDU itself completes the cycle and returns the outlet to ON automatically.`;
    }
    if (!confirm(warning)) return;
    const reason = prompt('Reason for protected-outlet override (required):');
    if (!reason || !reason.trim()) { showToast('Override rejected: a non-empty reason is required.'); return; }
    const indicator = rowIndicator(ip, outlet);
    indicator.className = 'cmd-indicator waiting';
    indicator.textContent = 'SENDING OVERRIDE REBOOT...';
    setPduButtons(ip, true);
    try {
        const response = await fetch('/api/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                ip, outlet, action: 'reboot',
                admin_override: true,
                acknowledge_protected_device: true,
                acknowledge_controller_may_go_offline: isSelfHost,
                reason: reason.trim()
            })
        });
        const data = await response.json();
        const errMsg = (data.error && data.error.message) ? data.error.message : data.error;
        if (!response.ok) throw new Error(errMsg || 'Command was rejected.');
        indicator.textContent = 'SENT OVERRIDE REBOOT - WAITING';
        showToast(`Override reboot sent to ${ip} outlet ${outlet}. Please wait for the PDU cycle to complete.`);
        await pollStatus();
    } catch (error) {
        indicator.className = 'cmd-indicator failed';
        indicator.textContent = `NOT SENT: ${error.message}`;
        setPduButtons(ip, false);
        showToast(error.message);
    }
}

async function sendSelected() {
    if (!selectedSequence.length) {
        showToast('Select at least one outlet first.');
        return;
    }
    const action = document.getElementById('batch-action').value;
    const pretty = action === 'reboot' ? 'CYCLE / REBOOT' : action.toUpperCase();
    const summary = selectedSequence.map((item, index) => `${index + 1}. ${item.ip} / Outlet ${item.outlet}`).join('\n');
    if (!confirm(`${pretty} the following ${selectedSequence.length} selected outlet(s) in this sequence?\n\n${summary}`)) return;

    selectedSequence.forEach(item => {
        const indicator = rowIndicator(item.ip, item.outlet);
        indicator.className = 'cmd-indicator waiting';
        indicator.textContent = `QUEUED ${pretty}`;
        setPduButtons(item.ip, true);
    });

    try {
        const response = await fetch('/api/batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action, items: selectedSequence.map(({ip, outlet}) => ({ip, outlet}))})
        });
        const data = await response.json();
        const errMsgB = (data.error && data.error.message) ? data.error.message : data.error;
        if (!response.ok) throw new Error(errMsgB || 'Batch was rejected.');
        showToast(`Batch dispatched to ${Object.keys(data.jobs).length} PDU(s). Please wait for command(s) to be processed.`);
        await pollStatus();
    } catch (error) {
        selectedSequence.forEach(item => {
            const indicator = rowIndicator(item.ip, item.outlet);
            indicator.className = 'cmd-indicator failed';
            indicator.textContent = `NOT SENT: ${error.message}`;
            setPduButtons(item.ip, false);
        });
        showToast(error.message);
    }
}

function setDisplayedState(ip, outlet, finalState) {
    const normalized = String(finalState || 'UNKNOWN').toUpperCase();
    const state = document.getElementById(`state-${safeIp(ip)}-${outlet}`);
    if (state) {
        state.textContent = normalized;
        state.className = `state ${normalized}`;
    }

    const row = document.getElementById(`row-${safeIp(ip)}-${outlet}`);
    if (row) {
        row.classList.remove('power-on', 'power-off', 'power-unknown');
        row.classList.add(`power-${normalized.toLowerCase()}`);
    }

    const box = document.querySelector(`.outlet-select[data-ip="${ip}"][data-outlet="${outlet}"]`);
    if (box) box.dataset.state = normalized;
}

function applyRuntime(ip, runtime) {
    const status = document.getElementById(`pdu-status-${safeIp(ip)}`);
    if (!status) return;

    setPduButtons(ip, Boolean(runtime.busy));
    if (runtime.busy) {
        status.className = 'pdu-status busy';
        const batch = runtime.queue_total > 1 ? ` ${runtime.queue_index}/${runtime.queue_total}` : '';
        status.textContent = `PROCESSING${batch} ${String(runtime.action || '').toUpperCase()} | Outlet ${runtime.current_outlet} | WAITING ${runtime.elapsed.toFixed(1)}s`;
        if (runtime.current_outlet) {
            const indicator = rowIndicator(ip, runtime.current_outlet);
            if (indicator) {
                indicator.className = 'cmd-indicator waiting';
                indicator.textContent = `SENT ${String(runtime.action || '').toUpperCase()} - WAITING ${runtime.elapsed.toFixed(1)}s (max 30s)`;
            }
        }
    } else {
        status.className = runtime.last_status === 'failed' ? 'pdu-status failed' : 'pdu-status';
        status.textContent = runtime.last_status === 'failed' ? `READY - LAST FAILED: ${runtime.last_message}` : 'READY';

        (runtime.results || []).forEach(result => {
            const indicator = rowIndicator(ip, result.outlet);
            if (indicator) {
                indicator.className = result.status === 'success' ? 'cmd-indicator success' : 'cmd-indicator failed';
                indicator.textContent = result.status === 'success' ? `${result.message}` : `FAILED: ${result.message}`;
            }
            if (result.final_state) setDisplayedState(ip, result.outlet, result.final_state);
        });
    }

    if (lastBusy[ip] && !runtime.busy) {
        showToast(`${runtime.asset_id || ip}: ${runtime.last_message}`, 8000);
    }
    lastBusy[ip] = Boolean(runtime.busy);
}

async function pollStatus() {
    try {
        const response = await fetch('/api/status', {cache: 'no-store'});
        if (!response.ok) return;
        const data = await response.json();
        Object.entries(data.pdus).forEach(([ip, runtime]) => applyRuntime(ip, runtime));
    } catch (error) {
        console.error(error);
    }
}

async function saveConfiguration() {
    const action = document.getElementById('batch-action').value;
    const response = await fetch('/configuration/export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, items: selectedSequence.map(({ip, outlet}) => ({ip, outlet}))})
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        showToast(data.error || 'Could not export configuration.');
        return;
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match ? match[1] : 'pdu-batch-configuration.md';
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    URL.revokeObjectURL(link.href);
    link.remove();
}

async function uploadConfiguration(input) {
    if (!input.files || !input.files[0]) return;
    const form = new FormData();
    form.append('file', input.files[0]);
    try {
        const response = await fetch('/configuration/upload', {method: 'POST', body: form});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Configuration upload failed.');
        selectedSequence.splice(0, selectedSequence.length, ...data.items);
        document.getElementById('batch-action').value = data.action;
        lastSelectionBox = null;
        renderSelectionOrder();
        showToast(`Loaded ${data.items.length} selected outlet(s) from ${input.files[0].name}. Review, then press SEND TO SELECTED.`, 9000);
    } catch (error) {
        showToast(error.message, 9000);
    } finally {
        input.value = '';
    }
}

const dragSelection = {
    timer: null,
    active: false,
    targetState: true,
    touched: new Set(),
    pointerId: null,
    handle: null
};

function clearDragTimer() {
    if (dragSelection.timer) {
        clearTimeout(dragSelection.timer);
        dragSelection.timer = null;
    }
}

function applyDragSelection(box) {
    const key = keyFor(box.dataset.ip, box.dataset.outlet);
    if (dragSelection.touched.has(key)) return;
    dragSelection.touched.add(key);
    setBoxSelected(box, dragSelection.targetState);
    renderSelectionOrder();
}

function endDragSelection() {
    clearDragTimer();
    if (dragSelection.handle) dragSelection.handle.classList.remove('active');
    dragSelection.active = false;
    dragSelection.touched.clear();
    dragSelection.pointerId = null;
    dragSelection.handle = null;
    document.body.classList.remove('drag-selecting');
}

function setupTouchDragSelection() {
    document.querySelectorAll('.drag-select-handle').forEach(handle => {
        handle.addEventListener('pointerdown', event => {
            if (event.pointerType === 'mouse') return;
            event.preventDefault();
            endDragSelection();

            const box = handle.closest('.select-cell').querySelector('.outlet-select');
            dragSelection.targetState = !box.checked;
            dragSelection.pointerId = event.pointerId;
            dragSelection.handle = handle;

            try { handle.setPointerCapture(event.pointerId); } catch (error) {}

            dragSelection.timer = setTimeout(() => {
                dragSelection.active = true;
                dragSelection.touched.clear();
                handle.classList.add('active');
                document.body.classList.add('drag-selecting');
                applyDragSelection(box);
                if (navigator.vibrate) navigator.vibrate(25);
            }, 450);
        });

        handle.addEventListener('pointermove', event => {
            if (!dragSelection.active || event.pointerId !== dragSelection.pointerId) return;
            event.preventDefault();
            const element = document.elementFromPoint(event.clientX, event.clientY);
            const row = element ? element.closest('.outlet') : null;
            const box = row ? row.querySelector('.outlet-select') : null;
            if (box) applyDragSelection(box);
        }, {passive: false});

        handle.addEventListener('pointerup', endDragSelection);
        handle.addEventListener('pointercancel', endDragSelection);
        handle.addEventListener('lostpointercapture', endDragSelection);
    });
}

try {
    setToolbarCollapsed(localStorage.getItem('pduToolbarCollapsed') === '1', false);
} catch (error) {
    setToolbarCollapsed(false, false);
}

renderSelectionOrder();
setupTouchDragSelection();
updateStickyOffset();

if (window.ResizeObserver) {
    const toolbarObserver = new ResizeObserver(updateStickyOffset);
    toolbarObserver.observe(document.getElementById('global-toolbar'));
}
window.addEventListener('resize', updateStickyOffset);
window.addEventListener('orientationchange', () => setTimeout(updateStickyOffset, 100));

pollStatus();
setInterval(pollStatus, 500);
</script>
</body>
</html>
"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — MIAM Rack PDU Control</title>
<style>
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:#151622; color:#e8eaf3; font-family:Inter,Arial,Helvetica,sans-serif; }
.card { width:min(400px, 92vw); background:#242635; border:1px solid #35384b; border-radius:12px;
        padding:28px; box-shadow:0 10px 30px rgba(0,0,0,.35); }
h1 { font-size:20px; margin:0 0 6px 0; color:#7eb2ff; }
.sub { color:#aeb3c4; font-size:13px; margin-bottom:22px; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#9fb2db; margin:18px 0 8px 0; }
label { display:block; font-size:12px; color:#c2c8dc; margin:10px 0 4px 0; }
input { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:7px; border:1px solid #4c526b;
        background:#1d1f2c; color:#fff; font-size:14px; }
button { margin-top:14px; width:100%; padding:11px; border:0; border-radius:7px; font-weight:800;
         font-size:13px; cursor:pointer; background:#6478d8; color:#fff; }
button.emergency { background:#7e4250; }
.divider { border-top:1px dashed #41465d; margin:20px 0 6px 0; }
.flash { padding:10px 12px; margin-bottom:12px; border-radius:7px; background:#4d2d35;
         color:#ffd1d9; font-size:13px; white-space:pre-wrap; }
.note { color:#8a90a8; font-size:11px; margin-top:14px; line-height:1.5; }
</style>
</head>
<body>
<div class="card">
    <h1>Rack PDU Power Control Center</h1>
    <div class="sub">MIAM — VM154</div>
    {% if error %}<div class="flash">{{ error }}</div>{% endif %}
    <form method="post" action="/login">
        <h2>LLDAP account</h2>
        <label for="username">Username</label>
        <input id="username" name="username" autocomplete="username" autofocus required>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit">Sign in</button>
    </form>
    <div class="divider"></div>
    <form method="post" action="/login">
        <input type="hidden" name="mode" value="emergency">
        <h2>Emergency local administrator</h2>
        <label for="e_user">User</label>
        <input id="e_user" name="e_user" autocomplete="off">
        <label for="e_pass">Password</label>
        <input id="e_pass" name="e_pass" type="password" autocomplete="off">
        <button type="submit" class="emergency">Emergency sign-in</button>
    </form>
    <div class="note">Use the emergency path only when the directory (LLDAP at 10.0.20.101) is unavailable.
    All sign-ins are audited. Protected outlets require administrative override with a reason.</div>
</div>
</body>
</html>
"""



# ----------------------------------------------------------------------------
# UI routes
# ----------------------------------------------------------------------------

@app.get("/")
def index():
    actor = g.actor
    view_pdus = []
    for pdu in rt.CONFIG["pdus"]:
        error = None
        try:
            states = rt.read_states(pdu["ip"])
        except Exception as exc:
            with rt.RUNTIME_LOCK:
                states = dict(rt.STATE_CACHE.get(pdu["ip"], {}))
            error = str(exc)

        protected = {int(value) for value in pdu.get("protected", [])}
        rows = []
        for outlet in range(1, int(pdu.get("outlets", 24)) + 1):
            info = states.get(outlet, {})
            rows.append(
                {
                    "number": outlet,
                    "label": rt.label_for(pdu, outlet),
                    "state": info.get("state", "UNKNOWN"),
                    "protected": outlet in protected,
                    "self_host": (pdu["ip"] == action_service.SELF_HOST_IP
                                  and outlet == action_service.SELF_HOST_OUTLET),
                }
            )

        view_pdus.append(
            {
                "name": pdu["name"],
                "asset_id": pdu.get("asset_id", ""),
                "ip": pdu["ip"],
                "rows": rows,
                "error": error,
            }
        )

    return render_template_string(
        HTML,
        pdus=view_pdus,
        backend=rt.backend_description(),
        audit=rt.recent_audit(),
        username=actor.username,
        auth_source=actor.auth_source,
        role_label=role_label(actor),
        can_override=actor.can_override_protected(),
        can_control=actor.can_control_normal(),
    )


@app.get("/api/status")
def api_status():
    return jsonify({"pdus": rt.runtime_snapshot(), "server_time": time.time()})


@app.post("/api/action")
def api_action():
    """Legacy UI dispatch route — now runs through the shared Action Service."""
    actor = g.actor
    payload = request.get_json(silent=True) or {}
    try:
        action = action_service.normalize_action(payload.get("action"))
        target = action_service.validate_target(payload.get("ip", ""), payload.get("outlet"), action)
        result = action_service.submit_action(
            actor,
            action,
            [target],
            reason=payload.get("reason", "") or "",
            admin_override=bool(payload.get("admin_override")),
            acknowledge_protected=bool(payload.get("acknowledge_protected_device")),
            acknowledge_controller_may_go_offline=bool(
                payload.get("acknowledge_controller_may_go_offline")),
            request_id=(payload.get("request_id") or None),
            source="ui",
        )
        return jsonify({"accepted": True, "jobs": result["jobs"]}), 202
    except ActionError as exc:
        return jsonify({"accepted": False, "error": exc.message}), exc.http_status
    except Exception as exc:
        return jsonify({"accepted": False, "error": str(exc)}), 409


@app.post("/api/batch")
def api_batch():
    """Legacy UI batch route — through the shared Action Service."""
    actor = g.actor
    payload = request.get_json(silent=True) or {}
    try:
        action = action_service.normalize_action(payload.get("action"))
        raw_items = payload.get("items") or []
        if not raw_items:
            raise ValueError("No outlets were selected.")
        if len(raw_items) > MAX_BATCH_ITEMS:
            raise ValueError(f"A maximum of {MAX_BATCH_ITEMS} outlets may be selected.")

        seen = set()
        targets = []
        for raw in raw_items:
            key = (raw.get("ip", ""), int(raw.get("outlet", 0)))
            if key in seen:
                continue
            seen.add(key)
            targets.append(action_service.validate_target(raw.get("ip", ""), raw.get("outlet"), action))

        result = action_service.submit_action(
            actor,
            action,
            targets,
            reason=payload.get("reason", "") or "",
            admin_override=bool(payload.get("admin_override")),
            acknowledge_protected=bool(payload.get("acknowledge_protected_device")),
            acknowledge_controller_may_go_offline=bool(
                payload.get("acknowledge_controller_may_go_offline")),
            request_id=(payload.get("request_id") or None),
            source="ui",
        )
        rt.audit(
            f"action={action.upper()} batch selected={len(targets)} "
            f"jobs={json.dumps(result['jobs'], sort_keys=True)} result=BATCH_DISPATCHED",
            "web", actor=actor.username)
        return jsonify({"accepted": True, "jobs": result["jobs"], "selected": len(targets)}), 202
    except ActionError as exc:
        return jsonify({"accepted": False, "error": exc.message}), exc.http_status
    except Exception as exc:
        return jsonify({"accepted": False, "error": str(exc)}), 409


@app.get("/audit/download")
def audit_download():
    actor = g.actor
    if not actor.can_view():
        return jsonify({"error": "Not authorized."}), 403
    Path(AUDIT_FILE).touch(exist_ok=True)
    filename = "pdu-control-full-audit-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"
    return send_file(
        AUDIT_FILE,
        mimetype="text/plain",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@app.post("/configuration/export")
def configuration_export():
    actor = g.actor
    payload = request.get_json(silent=True) or {}
    try:
        action = action_service.normalize_action(payload.get("action") or "on")
        raw_items = payload.get("items") or []
        validated = []
        seen = set()
        for raw in raw_items:
            # Export validates with 'on' so protected items can be saved without
            # implying permission; execution-time checks remain authoritative.
            item = validate_item(raw.get("ip", ""), raw.get("outlet"), "on")
            key = (item["ip"], item["outlet"])
            if key in seen:
                continue
            seen.add(key)
            validated.append({"ip": item["ip"], "outlet": item["outlet"]})

        markdown = build_markdown(action, validated)
        filename = "pdu-master-control-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".md"
        return Response(
            markdown,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/configuration/upload")
def configuration_upload():
    actor = g.actor
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "No Markdown file was uploaded."}), 400
    if not upload.filename.lower().endswith((".md", ".markdown", ".txt")):
        return jsonify({"error": "Upload a .md Markdown configuration file."}), 400

    raw = upload.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        return jsonify({"error": "Configuration file is larger than 1 MB."}), 400

    try:
        text = raw.decode("utf-8")
        action, items = parse_markdown_config(text)
        # Validate protected outlets against the imported action, but do not
        # execute. OFF/REBOOT on protected outlets is rejected at upload time;
        # override reboots are initiated per-outlet in the UI instead.
        for item in items:
            target = action_service.validate_target(item["ip"], item["outlet"], action)
            if target["protected"] and action in ("off", "reboot"):
                raise ValueError(
                    f"{target['pdu_id']} outlet {target['outlet']} is protected; "
                    "OFF/REBOOT selections are rejected on upload."
                )
        return jsonify({"action": action, "items": items, "count": len(items)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "backend": rt.backend_description(),
            "backend_mode": rt.backend_mode(),
            "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
            "pdus": [
                {
                    "asset_id": pdu.get("asset_id", ""),
                    "ip": pdu["ip"],
                    "name": pdu["name"],
                }
                for pdu in rt.CONFIG["pdus"]
            ],
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
