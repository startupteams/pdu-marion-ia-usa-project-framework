#!/usr/bin/env python3
"""
Central Action Service for PDU Manager (VM154) - V3 plan section 11/23.

ONE shared path for UI + API power operations:

    authentication context
    -> authorization
    -> protection check (HARD state machine, section 29)
    -> idempotency
    -> audit pre-record
    -> per-PDU lock (existing RUNTIME machinery preserved)
    -> existing PDU driver (native atomic cycle for protected reboots)
    -> state verification (+ recovery-on for protected outlets)
    -> audit completion

NON-PROTECTED OUTLET
  ON/OFF/REBOOT permitted per authorization.

PROTECTED / CRITICAL OUTLET
  ON      permitted
  OFF     FORBIDDEN for everyone (no flag, group, or login bypasses this)
  REBOOT  only with administrative override + reason + protected acknowledgement,
          executed as ONE native PDU Cycle-Load operation (never OFF-then-ON),
          expected final state ON.

CONTROLLER SELF-DEPENDENCY (MIAM-00153 outlet 9 -> MIAM-00133)
  additionally requires acknowledge_controller_may_go_offline; verification is
  skipped while VM154 is down and reconciled after return.
"""

import json
import os
import threading
import time
import uuid
from collections import defaultdict

import app_runtime as rt

IDEMPOTENCY_PERSIST_FILE = "/var/lib/pdu-control/idempotency.json"


# ----------------------------------------------------------------------------
# Error taxonomy (V3 plan section 19)
# ----------------------------------------------------------------------------

class ActionError(Exception):
    def __init__(self, code, http_status, message):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message}}


def err_auth_required():      return ActionError("AUTH_REQUIRED", 401, "Authentication required.")
def err_auth_invalid():       return ActionError("AUTH_INVALID", 401, "Invalid credentials.")
def err_not_authorized(msg="You are not authorized for this action."):
    return ActionError("NOT_AUTHORIZED", 403, msg)
def err_unknown_pdu(ip):      return ActionError("UNKNOWN_PDU", 404, f"Unknown PDU: {ip}")
def err_unknown_outlet(pdu, outlet):
    return ActionError("UNKNOWN_OUTLET", 404, f"Outlet {outlet} is outside the configured range for {pdu}.")
def err_invalid_action():     return ActionError("INVALID_ACTION", 400, "Action must be ON, OFF, or REBOOT/CYCLE.")
def err_protected_off():      return ActionError(
    "PROTECTED_OFF_FORBIDDEN", 403,
    "Protected outlets must remain ON. Use an administrative REBOOT/CYCLE if a restart is required.")
def err_override_reason():    return ActionError("OVERRIDE_REASON_REQUIRED", 403, "A non-empty reason is required for administrative override.")
def err_protected_ack():      return ActionError("PROTECTED_ACK_REQUIRED", 403, "Protected-device acknowledgement is required for an override reboot.")
def err_selfhost_ack():       return ActionError("SELF_HOST_ACK_REQUIRED", 403,
    "This reboot affects MIAM-00133, which hosts VM154/PDU Manager. acknowledge_controller_may_go_offline is required.")
def err_busy(msg):            return ActionError("PDU_BUSY", 409, msg)
def err_conflict():           return ActionError("DUPLICATE_REQUEST_CONFLICT", 409,
    "The same request_id was already used for a different action or target set.")
def err_pdu_failure(msg):     return ActionError("PDU_SSH_FAILURE", 502, msg)


# ----------------------------------------------------------------------------
# Config helpers over the shared runtime config
# ----------------------------------------------------------------------------

SELF_HOST_IP = "10.0.20.153"
SELF_HOST_OUTLET = 9


def find_pdu(ip):
    for pdu in rt.CONFIG["pdus"]:
        if pdu["ip"] == ip:
            return pdu
    return None


def outlet_is_protected(pdu, outlet):
    return outlet in {int(v) for v in pdu.get("protected", [])}


def outlet_is_self_host(ip, outlet):
    return ip == SELF_HOST_IP and outlet == SELF_HOST_OUTLET


# ----------------------------------------------------------------------------
# Idempotency store (V3 plan section 15) - in-memory, 24h retention default
# ----------------------------------------------------------------------------

_idem_lock = threading.Lock()
_IDEMPOTENCY = {}  # request_id -> {"actor", "action", "targets", "job_ids", "created"}


def _env_float(name, default):
    try:
        return float(__import__("os").environ.get(name, default))
    except (TypeError, ValueError):
        return default


IDEMPOTENCY_RETENTION_SECONDS = _env_float("PDU_IDEMPOTENCY_RETENTION", 24 * 3600)


def _prune_idempotency(now):
    cutoff = now - IDEMPOTENCY_RETENTION_SECONDS
    for key in [k for k, v in _IDEMPOTENCY.items() if v["created"] < cutoff]:
        _IDEMPOTENCY.pop(key, None)


def idempotency_lookup(request_id, actor, action, targets):
    """
    Returns ("hit", existing_record), ("conflict", None) or ("miss", None).
    targets must be a sorted list of {"pdu_id": ip, "outlet": n} dicts.
    """
    if not request_id:
        return "miss", None
    key = json.dumps({"targets": targets}, sort_keys=True)
    with _idem_lock:
        _prune_idempotency(time.time())
        record = _IDEMPOTENCY.get(request_id)
        if record is None:
            return "miss", None
        if record["action"] != action or json.dumps({"targets": record["targets"]}, sort_keys=True) != key:
            return "conflict", None
        return "hit", record


def _persist_idempotency_locked():
    """Best-effort snapshot so replays survive service restarts."""
    try:
        os.makedirs(os.path.dirname(IDEMPOTENCY_PERSIST_FILE), exist_ok=True)
        tmp = IDEMPOTENCY_PERSIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(_IDEMPOTENCY, handle)
        os.replace(tmp, IDEMPOTENCY_PERSIST_FILE)
    except Exception:
        pass


def _load_idempotency():
    try:
        with open(IDEMPOTENCY_PERSIST_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            _IDEMPOTENCY.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


_load_idempotency()


def idempotency_store(request_id, actor, action, targets, job_ids):
    if not request_id:
        return
    with _idem_lock:
        _prune_idempotency(time.time())
        _IDEMPOTENCY[request_id] = {
            "actor": actor,
            "action": action,
            "targets": targets,
            "job_ids": job_ids,
            "created": time.time(),
        }
        _persist_idempotency_locked()


def idempotency_job_status(job_ids):
    """Aggregate job status for an idempotent replay response."""
    with rt.RUNTIME_LOCK:
        jobs = []
        for ip, job_id in job_ids.items():
            runtime = rt.PDU_RUNTIME.get(ip)
            if not runtime:
                continue
            jobs.append({
                "pdu_id": ip,
                "job_id": job_id,
                "status": "executing" if runtime.get("busy") else
                          ("succeeded" if runtime.get("last_status") == "success" else
                           ("failed" if runtime.get("last_status") == "failed" else "queued")),
            })
    return jobs


# ----------------------------------------------------------------------------
# Validation + authorization (central)
# ----------------------------------------------------------------------------

def normalize_action(action):
    action = (action or "").strip().lower()
    if action not in ("on", "off", "reboot"):
        raise err_invalid_action()
    return action


def validate_target(ip, outlet, action):
    pdu = find_pdu(ip)
    if not pdu:
        raise err_unknown_pdu(ip)
    try:
        outlet = int(outlet)
    except (TypeError, ValueError):
        raise err_unknown_outlet(ip, outlet)
    max_outlets = int(pdu.get("outlets", 24))
    if outlet < 1 or outlet > max_outlets:
        raise err_unknown_outlet(ip, outlet)
    return {
        "pdu_id": pdu.get("asset_id", ip),
        "ip": ip,
        "outlet": outlet,
        "label": rt.label_for(pdu, outlet),
        "pdu_name": pdu["name"],
        "asset_id": pdu.get("asset_id", ""),
        "protected": outlet_is_protected(pdu, outlet),
        "self_host": outlet_is_self_host(ip, outlet),
    }


def check_authorization(actor, action, targets, override, reason,
                        acknowledge_protected, acknowledge_self_host):
    """Central authorization + the HARD protected-outlet state machine.

    Runs BEFORE any command transmission (V3 plan section 14.2/23).
    """
    if actor is None:
        raise err_auth_required()

    # --- Hard invariant first: protected OFF is forbidden for EVERYONE. -----
    for target in targets:
        if target["protected"] and action == "off":
            # No admin_override, no group, no emergency root may bypass this.
            raise err_protected_off()

    protected_targets = [t for t in targets if t["protected"]]

    if protected_targets and action == "reboot":
        if not actor.can_override_protected():
            raise err_not_authorized(
                "Administrative override permission is required to reboot a protected outlet.")
        if not override:
            raise err_not_authorized("admin_override=true is required for a protected-outlet reboot.")
        if not (reason or "").strip():
            raise err_override_reason()
        if not acknowledge_protected:
            raise err_protected_ack()

    # --- Controller self-dependency extra acknowledgement --------------------
    for target in targets:
        if target["self_host"] and action == "reboot" and (target["protected"] or override):
            if not acknowledge_self_host:
                raise err_selfhost_ack()

    # --- Normal authorization -------------------------------------------------
    if action in ("on", "off", "reboot"):
        if not actor.can_control_normal():
            raise err_not_authorized("Power control requires the pdu-operator or pdu-admin group.")

    if (reason or "").strip() or override:
        pass  # reason required only for override, checked above


# ----------------------------------------------------------------------------
# Job submission (reuses the app's runtime/lock machinery)
# ----------------------------------------------------------------------------

def submit_action(actor, action, targets, reason="", admin_override=False,
                  acknowledge_protected=False, acknowledge_controller_may_go_offline=False,
                  request_id=None, source="api", correlation=None):
    """
    Single shared entry point (V3 plan section 23, Phase 2).

    targets: list of validated target dicts from validate_target().
    Returns dict: {"accepted": True, "jobs": {ip: job_id}, "job_ref": "..."}
    Raises ActionError on any rejection (before any command is transmitted).
    """
    action = normalize_action(action)

    groups = defaultdict(list)
    for target in targets:
        groups[target["ip"]].append(target)
    groups = dict(groups)

    # Authorization + invariants (all-or-nothing BEFORE dispatch)
    check_authorization(actor, action, targets, bool(admin_override), reason,
                        bool(acknowledge_protected), bool(acknowledge_controller_may_go_offline))

    # Idempotency
    target_set = sorted([{"pdu_id": t["ip"], "outlet": t["outlet"]} for t in targets],
                        key=lambda d: (d["pdu_id"], d["outlet"]))
    state, record = idempotency_lookup(request_id, actor.username, action, target_set)
    if state == "hit":
        return {"accepted": True, "replayed": True, "jobs": record["job_ids"],
                "job_ref": record["job_ids"]}
    if state == "conflict":
        raise err_conflict()

    # Audit pre-record (V3 plan section 14.2: before command transmission)
    rt.audit_write(
        f"submit action={action.upper()} actor={actor.username} auth={actor.auth_source} "
        f"override={bool(admin_override)} reason=\"{(reason or '').strip()}\" "
        f"targets={json.dumps(target_set, sort_keys=True)} "
        f"self_host_ack={bool(acknowledge_controller_may_go_offline)} "
        f"request_id={request_id or '-'} source={source} result=ACCEPTED",
        source=source,
    )
    # REV4 §10C Phase 6 — structured correlation record (JSONL): downstream services
    # (ACMS Work → InfrastructureOperation → Server Manager op → PDU job → physical audit)
    # correlate via these fields. correlation_id/source_service/upstream_operation_id are
    # parsed from the tagged reason (asset-addressed path) or explicit kwargs.
    rt.audit_write_structured({
        "event": "action_submitted",
        "action": action,
        "actor": actor.username,
        "auth": actor.auth_source,
        "request_id": request_id or "-",
        "targets": target_set,
        "source": source,
        "correlation_id": correlation.get("correlation_id"),
        "source_service": correlation.get("source_service"),
        "upstream_operation_id": correlation.get("upstream_operation_id"),
    })

    # Per-PDU lock via the existing reserve path; jobs run the shared worker.
    try:
        job_ids = rt.reserve_and_start_for_service(
            groups, action, actor, admin_override, request_id, source)
    except RuntimeError as exc:
        raise err_busy(str(exc))

    idempotency_store(request_id, actor.username, action, target_set, job_ids)
    return {"accepted": True, "replayed": False, "jobs": job_ids, "job_ref": job_ids}
