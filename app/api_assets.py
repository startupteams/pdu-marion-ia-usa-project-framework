"""PDU Manager — asset-addressed API (REV4 §10C Phase 2).

Asset mapping: derived from the Git-managed labels (FW-001 source of truth) —
the FIRST `MIAM-###` token in an outlet's label is the outlet's primary asset_id.
Callers address assets (MIAM-00119); the manager resolves PDU/outlet internally.
Raw PDU/outlet addressing continues to work for existing callers (Phase 3 adds
the expected_asset_id identity guard to that path).

Correlation metadata (§10C Phase 6, staged): every action accepts
correlation_id / source_service / upstream_operation_id and persists them in
the audit record via the action's reason tag + audit extra fields.
"""
from __future__ import annotations

import re

import action_service
import app_runtime as rt
from action_service import ActionError
from flask import Blueprint, jsonify, request

_ASSET_RE = re.compile(r"(MIAM-\d{5})")


def build_asset_index(pdu_list: list) -> dict:
    """asset_id → {asset_id, pdu_ip, pdu_asset_id, outlet, label}.

    First MIAM-### token in the label wins; duplicate asset_ids across outlets
    raise ValueError (config bug — Git-managed labels must be unambiguous).
    """
    index: dict[str, dict] = {}
    for pdu in pdu_list:
        labels = pdu.get("labels", {}) or {}
        for outlet_s, label in labels.items():
            m = _ASSET_RE.search(label or "")
            if not m:
                continue
            asset_id = m.group(1)
            if asset_id in index:
                raise ValueError(
                    f"duplicate asset_id {asset_id} in labels: "
                    f"{index[asset_id]['pdu_asset_id']}:{index[asset_id]['outlet']} and "
                    f"{pdu.get('asset_id')}:{outlet_s}")
            index[asset_id] = {
                "asset_id": asset_id,
                "pdu_ip": pdu["ip"],
                "pdu_asset_id": pdu.get("asset_id", pdu["ip"]),
                "outlet": int(outlet_s),
                "label": label,
            }
    return index


def _asset_payload(asset_id: str, index: dict, states: dict | None = None) -> dict:
    a = index[asset_id]
    pdu = rt.find_pdu(a["pdu_ip"])
    info = (states or {}).get(a["outlet"], {})
    return {
        "asset_id": asset_id,
        "pdu_id": a["pdu_asset_id"],
        "outlet": a["outlet"],
        "label": a["label"],
        "state": (info.get("state") or "UNKNOWN").upper()
                 if info.get("state") in ("On", "Off", "ON", "OFF", None) else info.get("state", "UNKNOWN"),
        "controllable": info.get("controllable", None),
        "protected": a["outlet"] in {int(v) for v in pdu.get("protected", [])},
    }


def register_asset_routes(bp: Blueprint, actor_or_response, request_id_from, error_response) -> None:
    """Attach asset routes to the /api/v1 blueprint, reusing the SAME auth chain,
    rate limits, idempotency, protected-policy, jobs and audit as raw outlet routes."""

    @bp.get("/assets")
    def list_assets():
        actor, failure = actor_or_response()
        if failure is not None:
            return failure
        pdu_list = rt.CONFIG["pdus"]
        try:
            index = build_asset_index(pdu_list)
        except ValueError as exc:
            resp = jsonify({"error": {"code": "ASSET_MAPPING_AMBIGUOUS", "message": str(exc)}})
            resp.status_code = 500
            return resp
        assets = [_asset_payload(x, index) for x in sorted(index)]
        return jsonify({"object": "list", "data": assets, "count": len(assets)})

    @bp.get("/assets/<asset_id>")
    def get_asset(asset_id: str):
        actor, failure = actor_or_response()
        if failure is not None:
            return failure
        try:
            index = build_asset_index(rt.CONFIG["pdus"])
        except ValueError as exc:
            resp = jsonify({"error": {"code": "ASSET_MAPPING_AMBIGUOUS", "message": str(exc)}})
            resp.status_code = 500
            return resp
        if asset_id not in index:
            resp = jsonify({"error": {"code": "ASSET_UNKNOWN",
                                      "message": f"no asset {asset_id!r} in the Git-managed mapping"}})
            resp.status_code = 404
            return resp
        a = index[asset_id]
        try:
            states = rt.read_states(a["pdu_ip"])
        except Exception as exc:
            return _pdu_error(exc)
        return jsonify(_asset_payload(asset_id, index, states))

    @bp.post("/assets/<asset_id>/actions")
    def submit_asset_action(asset_id: str):
        actor, failure = actor_or_response()
        if failure is not None:
            return failure
        try:
            index = build_asset_index(rt.CONFIG["pdus"])
        except ValueError as exc:
            resp = jsonify({"error": {"code": "ASSET_MAPPING_AMBIGUOUS", "message": str(exc)}})
            resp.status_code = 500
            return resp
        if asset_id not in index:
            resp = jsonify({"error": {"code": "ASSET_UNKNOWN",
                                      "message": f"no asset {asset_id!r} in the Git-managed mapping"}})
            resp.status_code = 404
            return resp
        a = index[asset_id]

        payload = request.get_json(silent=True) or {}
        # §10C Phase 6 correlation (staged): append to the audit reason; structured
        # correlation columns land with the audit schema extension.
        corr_bits = []
        for k in ("correlation_id", "source_service", "upstream_operation_id"):
            if payload.get(k):
                corr_bits.append(f"{k}={payload[k]}")
        try:
            action = action_service.normalize_action(payload.get("action"))
            target = action_service.validate_target(a["pdu_ip"], a["outlet"], action)
            result = action_service.submit_action(
                actor, action, [target],
                reason=((payload.get("reason", "") or "") + f" [asset {asset_id}"
                        + (";" + ";".join(corr_bits) if corr_bits else "") + "]"),
                admin_override=bool(payload.get("admin_override")),
                acknowledge_protected=bool(payload.get("acknowledge_protected_device")),
                acknowledge_controller_may_go_offline=bool(payload.get("acknowledge_controller_may_go_offline")),
                request_id=request_id_from(),
                source="api",
                correlation={k: payload.get(k) for k in
                             ("correlation_id", "source_service", "upstream_operation_id")
                             if payload.get(k)},
            )
        except ActionError as exc:
            return error_response(exc)

        resp = jsonify({
            "accepted": True,
            "replayed": result.get("replayed", False),
            "job_id": result["jobs"].get(a["pdu_ip"]),
            "status": "queued" if not result.get("replayed") else "replayed",
            "actor": actor.username,
            "asset_id": asset_id,
            "resolved": {"pdu_id": a["pdu_asset_id"], "outlet": a["outlet"]},
            "action": action,
        })
        resp.status_code = 202
        return resp


def _pdu_error(exc):
    from api_v1 import _pdu_error as f

    return f(exc)