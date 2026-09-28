#!/usr/bin/env python3
"""
REST API v1 for PDU Manager (V3 plan section 13).

Auth: HTTP Basic with LLDAP credentials (over HTTPS in production).
All responses JSON. Never HTML. Never arbitrary command execution.

Endpoints:
  GET  /api/v1/health                                   (no auth)
  GET  /api/v1/me
  GET  /api/v1/pdus
  GET  /api/v1/pdus/{ip}
  GET  /api/v1/pdus/{ip}/outlets
  GET  /api/v1/pdus/{ip}/outlets/{n}
  POST /api/v1/pdus/{ip}/outlets/{n}/actions
  GET  /api/v1/jobs/{job_id}                            (current run state)
  POST /api/v1/actions/batch

Rate limits (per identity, configurable): 120 GET/min, 20 POST/min.
"""

import json
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, Response, jsonify, request

import action_service
import app_runtime as rt
from action_service import ActionError
from auth_lldap import Actor, authenticate_lldap, LdapUnavailable

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

API_VERSION = "1.0.0"


# ----------------------------------------------------------------------------
# Rate limiting (V3 section 17)
# ----------------------------------------------------------------------------

def _env_int(name, default):
    try:
        return int(__import__("os").environ.get(name, default))
    except (TypeError, ValueError):
        return default


RATE_GET_PER_MIN = _env_int("PDU_RATE_GET_PER_MIN", 120)
RATE_POST_PER_MIN = _env_int("PDU_RATE_POST_PER_MIN", 20)

_rl_lock = threading.Lock()
_RATE_BUCKETS = defaultdict(lambda: {"get": deque(), "post": deque()})


def _rate_check(identity, kind):
    """True if allowed. Sliding 60s window."""
    now = time.time()
    limit = RATE_GET_PER_MIN if kind == "get" else RATE_POST_PER_MIN
    with _rl_lock:
        bucket = _RATE_BUCKETS[identity]
        q = bucket[kind]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _rate_limited_response(kind, identity):
    with _rl_lock:
        q = _RATE_BUCKETS[identity][kind]
        oldest = q[0] if q else time.time()
    retry_after = max(1, int(60 - (time.time() - oldest)))
    resp = jsonify({
        "error": {
            "code": "RATE_LIMITED",
            "message": f"Rate limit exceeded for {kind.upper()} requests ({retry_after}s window).",
        }
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


# ----------------------------------------------------------------------------
# Authentication helper (Basic over LLDAP)
# ----------------------------------------------------------------------------

def _request_id_from():
    """Idempotency-Key header or body request_id, normalized (V3 section 15)."""
    rid = request.headers.get("Idempotency-Key", "").strip()
    if rid:
        return rid[:200]
    body = request.get_json(silent=True) or {}
    rid = str(body.get("request_id") or "").strip()
    return rid[:200] if rid else None


def _current_actor_or_response():
    """Returns (Actor, None) on success, (None, Response) on failure.

    Basic-auth only for /api/v1 (V3 section 21: no local-root Basic on the API).
    """
    auth = request.authorization
    if not auth or not auth.username or not auth.password:
        resp = jsonify({
            "error": {"code": "AUTH_REQUIRED",
                      "message": "HTTP Basic authentication with LLDAP credentials is required."}
        })
        resp.status_code = 401
        resp.headers["WWW-Authenticate"] = 'Basic realm="PDU Manager API v1"'
        return None, resp

    username = auth.username
    identity = f"api:{username}"
    kind = "get" if request.method in ("GET", "HEAD") else "post"

    # Rate limit BEFORE LDAP bind (protects LLDAP from hammering too)
    if not _rate_check(identity, kind):
        return None, _rate_limited_response(kind, identity)

    try:
        actor = authenticate_lldap(username, auth.password)
    except LdapUnavailable as exc:
        resp = jsonify({
            "error": {"code": "AUTH_LDAP_UNAVAILABLE",
                      "message": f"LLDAP unreachable: {exc}"}
        })
        resp.status_code = 503
        return None, resp

    if actor is None:
        resp = jsonify({
            "error": {"code": "AUTH_INVALID", "message": "Invalid LLDAP credentials."}
        })
        resp.status_code = 401
        return None, resp

    if not actor.can_view():
        resp = jsonify({
            "error": {"code": "NOT_AUTHORIZED",
                      "message": "Account has no PDU access group (pdu-viewer/pdu-operator/pdu-admin)."}
        })
        resp.status_code = 403
        return None, resp

    return actor, None


def _error_response(exc):
    resp = jsonify(exc.to_dict())
    resp.status_code = exc.http_status
    return resp


# ----------------------------------------------------------------------------
# Health (no auth)
# ----------------------------------------------------------------------------

@api_v1.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "api_version": API_VERSION,
        "backend": rt.backend_description(),
        "time": rt.utc_iso(),
    })


# ----------------------------------------------------------------------------
# Identity
# ----------------------------------------------------------------------------

@api_v1.get("/me")
def me():
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure
    return jsonify(actor.as_dict())


# ----------------------------------------------------------------------------
# Inventory + state
# ----------------------------------------------------------------------------

def _outlet_payload(pdu, outlet, states):
    info = states.get(outlet, {})
    return {
        "pdu_id": pdu.get("asset_id", pdu["ip"]),
        "ip": pdu["ip"],
        "outlet": outlet,
        "name": rt.label_for(pdu, outlet),
        "state": info.get("state", "UNKNOWN"),
        "controllable": info.get("controllable", None),
        "protected": outlet in {int(v) for v in pdu.get("protected", [])},
        "self_host_dependency": (pdu["ip"] == action_service.SELF_HOST_IP
                                 and outlet == action_service.SELF_HOST_OUTLET),
        "last_verified_at": rt.utc_iso() if info else None,
    }


@api_v1.get("/pdus")
def list_pdus():
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure
    snapshot = rt.runtime_snapshot()
    result = []
    for pdu in rt.CONFIG["pdus"]:
        runtime = snapshot.get(pdu["ip"], {})
        result.append({
            "pdu_id": pdu.get("asset_id", pdu["ip"]),
            "ip": pdu["ip"],
            "name": pdu["name"],
            "outlets": int(pdu.get("outlets", 24)),
            "protected_outlets": sorted(int(v) for v in pdu.get("protected", [])),
            "busy": bool(runtime.get("busy")),
            "job_id": runtime.get("job_id"),
        })
    return jsonify({"pdus": result})


@api_v1.get("/pdus/<path:pdu_key>")
def get_pdu(pdu_key):
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure
    ip = _resolve_pdu(pdu_key)
    if ip is None:
        return _error_response(action_service.err_unknown_pdu(pdu_key))
    pdu = rt.find_pdu(ip)
    try:
        states = rt.read_states(ip)
    except Exception as exc:
        return _pdu_error(exc)
    outlets = [_outlet_payload(pdu, n, states)
               for n in range(1, int(pdu.get("outlets", 24)) + 1)]
    return jsonify({
        "pdu_id": pdu.get("asset_id", ip),
        "ip": ip,
        "name": pdu["name"],
        "outlets": outlets,
    })


@api_v1.get("/pdus/<path:pdu_key>/outlets")
def list_outlets(pdu_key):
    return get_pdu(pdu_key)


@api_v1.get("/pdus/<path:pdu_key>/outlets/<int:outlet>")
def get_outlet(pdu_key, outlet):
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure
    ip = _resolve_pdu(pdu_key)
    if ip is None:
        return _error_response(action_service.err_unknown_pdu(pdu_key))
    try:
        target = action_service.validate_target(ip, outlet, "on")  # 'on' = validation-only
    except ActionError as exc:
        return _error_response(exc)
    try:
        states = rt.read_states(ip)
    except Exception as exc:
        return _pdu_error(exc)
    return jsonify(_outlet_payload(rt.find_pdu(ip), target["outlet"], states))


def _resolve_pdu(pdu_key):
    """Accept asset_id (MIAM-00153) or IP (10.0.20.153)."""
    pdu_key = (pdu_key or "").strip()
    for pdu in rt.CONFIG["pdus"]:
        if pdu_key == pdu["ip"] or pdu_key == pdu.get("asset_id", ""):
            return pdu["ip"]
    return None


def _pdu_error(exc):
    resp = jsonify({
        "error": {"code": "PDU_SSH_FAILURE", "message": str(exc)[-500:]}
    })
    resp.status_code = 502
    return resp


# ----------------------------------------------------------------------------
# Action submission
# ----------------------------------------------------------------------------

def _source_meta():
    return {
        "ip": request.remote_addr or "unknown",
        "user_agent": (request.user_agent.string or "")[:200],
        "interface": "api",
    }


@api_v1.post("/pdus/<path:pdu_key>/outlets/<int:outlet>/actions")
def submit_outlet_action(pdu_key, outlet):
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure

    ip = _resolve_pdu(pdu_key)
    if ip is None:
        return _error_response(action_service.err_unknown_pdu(pdu_key))

    payload = request.get_json(silent=True) or {}
    try:
        action = action_service.normalize_action(payload.get("action"))
        target = action_service.validate_target(ip, outlet, action)
        result = action_service.submit_action(
            actor,
            action,
            [target],
            reason=payload.get("reason", "") or "",
            admin_override=bool(payload.get("admin_override")),
            acknowledge_protected=bool(payload.get("acknowledge_protected_device")),
            acknowledge_controller_may_go_offline=bool(payload.get("acknowledge_controller_may_go_offline")),
            request_id=_request_id_from(),
            source="api",
        )
    except ActionError as exc:
        return _error_response(exc)

    resp = jsonify({
        "accepted": True,
        "replayed": result.get("replayed", False),
        "job_id": result["jobs"].get(ip),
        "status": "queued" if not result.get("replayed") else "replayed",
        "actor": actor.username,
        "pdu_id": target["pdu_id"],
        "outlet": target["outlet"],
        "action": action,
    })
    resp.status_code = 202
    return resp


@api_v1.post("/actions/batch")
def batch_action():
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure

    payload = request.get_json(silent=True) or {}
    try:
        action = action_service.normalize_action(payload.get("action"))
        raw_targets = payload.get("targets") or []
        if not raw_targets:
            raise ActionError("INVALID_REQUEST", 400, "No targets provided.")
        if len(raw_targets) > rt.MAX_BATCH_ITEMS:
            raise ActionError("INVALID_REQUEST", 400,
                              f"A maximum of {rt.MAX_BATCH_ITEMS} targets is allowed.")

        seen = set()
        targets = []
        for raw in raw_targets:
            ip = _resolve_pdu(str(raw.get("pdu_id") or raw.get("ip") or ""))
            if ip is None:
                raise action_service.err_unknown_pdu(str(raw.get("pdu_id") or raw.get("ip")))
            try:
                outlet = int(raw.get("outlet"))
            except (TypeError, ValueError):
                raise action_service.err_unknown_outlet(ip, raw.get("outlet"))
            key = (ip, outlet)
            if key in seen:
                continue
            seen.add(key)
            targets.append(action_service.validate_target(ip, outlet, action))

        result = action_service.submit_action(
            actor,
            action,
            targets,
            reason=payload.get("reason", "") or "",
            admin_override=bool(payload.get("admin_override")),
            acknowledge_protected=bool(payload.get("acknowledge_protected_device")),
            acknowledge_controller_may_go_offline=bool(payload.get("acknowledge_controller_may_go_offline")),
            request_id=_request_id_from(),
            source="api",
        )
    except ActionError as exc:
        return _error_response(exc)

    resp = jsonify({
        "accepted": True,
        "replayed": result.get("replayed", False),
        "jobs": result["jobs"],
        "targets": len(targets),
        "action": action,
    })
    resp.status_code = 202
    return resp


# ----------------------------------------------------------------------------
# Job status (current-run view; V3 13.5 states mapped onto runtime)
# ----------------------------------------------------------------------------

@api_v1.get("/jobs/<job_id>")
def job_status(job_id):
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure

    with rt.RUNTIME_LOCK:
        for ip, runtime in rt.PDU_RUNTIME.items():
            if runtime.get("job_id") == job_id:
                state = _map_job_state(runtime)
                return jsonify({
                    "job_id": job_id,
                    "pdu_id": runtime.get("ip"),
                    "state": state,
                    "action": runtime.get("action"),
                    "current_outlet": runtime.get("current_outlet"),
                    "queue_index": runtime.get("queue_index"),
                    "queue_total": runtime.get("queue_total"),
                    "message": runtime.get("message"),
                    "last_status": runtime.get("last_status"),
                    "last_message": runtime.get("last_message"),
                    "results": runtime.get("results", []),
                })
    with rt.WAITING_LOCK:
        for ip, queue in rt.WAITING_JOBS.items():
            for items, action, meta, queued_job_id in queue:
                if queued_job_id == job_id:
                    return jsonify({
                        "job_id": job_id,
                        "pdu_id": ip,
                        "state": "waiting_for_pdu_lock",
                        "action": action,
                        "message": "Queued behind an active operation on this PDU.",
                    })
    with rt.RUNTIME_LOCK:
        completed = rt.COMPLETED_JOBS.get(job_id)
    if completed:
        return jsonify(completed)
    resp = jsonify({
        "error": {"code": "UNKNOWN_JOB", "message": "Job not found (jobs are visible while active/recent, or in the last 200 completed)."}
    })
    resp.status_code = 404
    return resp


def _map_job_state(runtime):
    if runtime.get("busy"):
        message = (runtime.get("message") or "")
        if "WAITING_FOR_PDU_LOCK" in message:
            return "waiting_for_pdu_lock"
        return "executing"
    last = runtime.get("last_status")
    if last == "success":
        return "succeeded"
    if last == "failed":
        return "failed"
    if last == "pending_reconcile":
        return "verifying"
    return "queued"


# ----------------------------------------------------------------------------
# Audit export (human-readable, from the structured log)
# ----------------------------------------------------------------------------

@api_v1.get("/audit")
def audit_tail():
    actor, failure = _current_actor_or_response()
    if failure is not None:
        return failure
    if not actor.can_view():
        resp = jsonify({"error": {"code": "NOT_AUTHORIZED",
                                  "message": "Audit access requires pdu-viewer or pdu-admin."}})
        resp.status_code = 403
        return resp
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 500))
    try:
        with open(rt.AUDIT_FILE + ".jsonl", "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        lines = []
    return jsonify({"entries": [json.loads(l) for l in lines[-limit:]]})


# ----------------------------------------------------------------------------
# Asset-addressed API (REV4 §10C Phase 2) — registered after all raw routes
# ----------------------------------------------------------------------------
from api_assets import register_asset_routes

register_asset_routes(api_v1, _current_actor_or_response, _request_id_from, _error_response)
