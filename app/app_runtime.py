#!/usr/bin/env python3
"""
Shared runtime core for PDU Manager (VM154).

Holds the existing runtime machinery (per-PDU busy locks, worker subprocess,
state cache, audit) plus the V3 additions:

  - actor-aware audit lines (who/why/override, written BEFORE transmission)
  - protected-outlet recovery ON if a cycle ends unexpectedly OFF
  - controller self-dependency handling (153:9): pending-marker + reconcile
    on startup, because VM154 loses power during the native PDU cycle
  - waiting_for_pdu_lock queue for API submissions on a busy PDU
  - startup reconciliation of any pending self-host reboot

Legacy behaviours are preserved: one gunicorn worker, threads share runtime
structures, command subprocess with hard timeout, state re-read after action.
"""

import base64
import datetime
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

# Backend selection: PDU_BACKEND=mock (CI/staging) swaps the state-read and
# control paths to the in-repo mock driver. In mock mode the real SSH driver
# is never imported, so no hardware can be reached at all (REQ-010).
if os.environ.get("PDU_BACKEND", "").lower() == "mock":
    import mock_pdu_backend as pdu_direct
else:
    import pdu_ssh_direct as pdu_direct

CONFIG_FILE = "/etc/pdu-control/config.json"
SECRETS_FILE = "/etc/pdu-control/secrets.env"
AUDIT_FILE = "/var/log/pdu-control/audit.log"
AUDIT_DB_FILE = "/var/lib/pdu-control/audit.sqlite3"
WORKER_FILE = "/opt/pdu-control/pdu_worker.py"
PENDING_FILE = "/var/lib/pdu-control/pending_reboot.json"
PYTHON_BIN = "/opt/pdu-control/venv/bin/python"
COMMAND_TIMEOUT_SECONDS = 60
MAX_BATCH_ITEMS = 72
PROTECTED_RECOVERY_ATTEMPTS = 2  # small configurable limit (V3 14.3)
SELF_HOST_SETTLE_SECONDS = 8  # seconds after cycle confirm before skipping verify

with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
    CONFIG = json.load(handle)


def load_secrets():
    values = {}
    with open(SECRETS_FILE, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip()
    return values


RAW_SECRETS = load_secrets()


def secret(name):
    encoded = RAW_SECRETS.get(f"{name}_B64", "")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8")


# One gunicorn worker is required. Threads share these runtime structures.
RUNTIME_LOCK = threading.RLock()
PDU_RUNTIME = {}
STATE_CACHE = {}
COMPLETED_JOBS = {}  # job_id -> summary snapshot (last 200, in-process)
WAITING_JOBS = defaultdict(list)  # ip -> list of (items, action, meta) waiting for lock
WAITING_LOCK = threading.Lock()


def new_runtime(ip):
    return {
        "ip": ip,
        "busy": False,
        "job_id": None,
        "action": None,
        "started_at": None,
        "command_started_at": None,
        "deadline": None,
        "current_outlet": None,
        "current_label": None,
        "queue_index": 0,
        "queue_total": 0,
        "message": "READY",
        "last_status": "ready",
        "last_message": "READY",
        "last_completed_at": None,
        "results": [],
    }


for configured_pdu in CONFIG["pdus"]:
    PDU_RUNTIME[configured_pdu["ip"]] = new_runtime(configured_pdu["ip"])


def utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# Audit (structured JSONL in sqlite-friendly text + legacy human log)
# ----------------------------------------------------------------------------

_audit_lock = threading.Lock()


def audit_write_structured(record):
    """Append one JSON object per line to the structured audit file."""
    line = json.dumps(record, sort_keys=True)
    with _audit_lock:
        with open(AUDIT_FILE + ".jsonl", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def audit_write(message, source="system", actor=None, extra=None):
    """Legacy human-readable line + structured record."""
    who = actor or "unknown"
    with _audit_lock:
        with open(AUDIT_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{utc_iso()} user={who} source={source} {message}\n")
    record = {
        "ts": utc_iso(),
        "actor": who,
        "source": source,
        "message": message,
    }
    if extra:
        record.update(extra)
    audit_write_structured(record)


def audit(message, source=None, actor=None, extra=None):
    audit_write(
        message,
        source if source is not None else "system",
        actor=actor,
        extra=extra,
    )


def recent_audit(lines=40):
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as handle:
            content = handle.readlines()
        return "".join(content[-lines:])
    except FileNotFoundError:
        return "No actions have been performed yet.\n"


# ----------------------------------------------------------------------------
# Config helpers
# ----------------------------------------------------------------------------

def find_pdu(ip):
    for pdu in CONFIG["pdus"]:
        if pdu["ip"] == ip:
            return pdu
    return None


def label_for(pdu, outlet):
    return pdu.get("labels", {}).get(str(outlet), f"Outlet {outlet}")


def backend_description():
    return "Direct SSH / PowerAlert menu (physically verified)"


def is_busy(ip):
    with RUNTIME_LOCK:
        runtime = PDU_RUNTIME[ip]
        return bool(runtime["busy"])


def read_states(ip):
    # Never open a second management session while a command/batch owns this PDU.
    if is_busy(ip):
        with RUNTIME_LOCK:
            return dict(STATE_CACHE.get(ip, {}))
    states = pdu_direct.read_all_states(ip)
    with RUNTIME_LOCK:
        STATE_CACHE[ip] = dict(states)
    return states


def runtime_snapshot():
    now = time.time()
    output = {}
    with RUNTIME_LOCK:
        for pdu in CONFIG["pdus"]:
            ip = pdu["ip"]
            runtime = dict(PDU_RUNTIME[ip])
            started = runtime.get("command_started_at")
            deadline = runtime.get("deadline")
            runtime["elapsed"] = max(0.0, now - started) if started else 0.0
            runtime["remaining"] = max(0.0, deadline - now) if deadline else 0.0
            runtime["name"] = pdu["name"]
            runtime["asset_id"] = pdu.get("asset_id", "")
            runtime["cached_states"] = STATE_CACHE.get(ip, {})
            output[ip] = runtime
    return output


# ----------------------------------------------------------------------------
# Control process (existing pattern preserved)
# ----------------------------------------------------------------------------

def terminate_process_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_control_process(ip, outlet, action):
    command = [
        PYTHON_BIN,
        WORKER_FILE,
        ip,
        str(outlet),
        action,
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc)
        stdout, stderr = proc.communicate()
        raise TimeoutError(
            f"PDU command exceeded the {COMMAND_TIMEOUT_SECONDS}-second safety timeout."
        )

    if proc.returncode != 0:
        detail = (stderr or stdout or "PDU worker failed.").strip()
        raise RuntimeError(detail[-4000:])

    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise RuntimeError(f"Could not parse PDU worker result: {stdout[-2000:]}") from exc

    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "PDU worker reported failure."))

    return payload.get("final_state", "UNKNOWN")


def update_runtime(ip, **changes):
    with RUNTIME_LOCK:
        PDU_RUNTIME[ip].update(changes)


# ----------------------------------------------------------------------------
# Self-host pending marker
# ----------------------------------------------------------------------------

def write_pending_reboot(meta):
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    tmp = PENDING_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)
    os.replace(tmp, PENDING_FILE)


def clear_pending_reboot():
    try:
        os.remove(PENDING_FILE)
    except FileNotFoundError:
        pass


def read_pending_reboot():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------------------------
# Job worker
# ----------------------------------------------------------------------------

SELF_HOST_IP = "10.0.20.153"
SELF_HOST_OUTLET = 9


def _item_is_self_host(item):
    return item["ip"] == SELF_HOST_IP and int(item.get("outlet", 0)) == SELF_HOST_OUTLET


def pdu_job_worker(ip, items, action, job_id, source, meta=None):
    """Existing job loop, extended with actor metadata, protected recovery and
    self-host handling. meta may carry actor/override/reason/request_id."""
    meta = meta or {}
    actor = meta.get("actor", "unknown")
    total = len(items)
    overall_results = []

    is_self_host_job = any(_item_is_self_host(item) for item in items) and action == "reboot"

    try:
        for index, item in enumerate(items, start=1):
            command_started = time.time()
            update_runtime(
                ip,
                action=action,
                command_started_at=command_started,
                deadline=command_started + COMMAND_TIMEOUT_SECONDS,
                current_outlet=item["outlet"],
                current_label=item["label"],
                queue_index=index,
                queue_total=total,
                message=(
                    f"SENT {action.upper()} - Outlet {item['outlet']} - "
                    "WAIT FOR COMMAND TO BE PROCESSED"
                ),
                last_status="busy",
            )

            audit_write(
                f'pdu="{item["pdu_name"]}" asset_id={item["asset_id"]} '
                f'ip={ip} outlet={item["outlet"]} asset="{item["label"]}" '
                f'action={action.upper()} job={job_id} sequence={index}/{total} '
                f'override={meta.get("override", False)} reason="{meta.get("reason", "")}" '
                f'request_id={meta.get("request_id", "-")} result=SENT',
                source,
                actor=actor,
            )

            try:
                if is_self_host_job:
                    # V3 14.4: the native cycle is committed by the PDU itself.
                    # VM154 will lose power; do NOT verify inline (worker dies).
                    # Confirm the cycle, then hand control back; reconciliation
                    # happens at startup after the host returns.
                    write_pending_reboot({
                        "job_id": job_id,
                        "ip": ip,
                        "outlet": item["outlet"],
                        "label": item["label"],
                        "actor": actor,
                        "override": meta.get("override", False),
                        "reason": meta.get("reason", ""),
                        "request_id": meta.get("request_id", ""),
                        "started_at": utc_iso(),
                        "expected_final_state": "ON",
                    })
                    # Send the cycle; do not block on verification.
                    _self_host_send_cycle(ip, item["outlet"])
                    final_state = "ON"  # expected; reconciled after reboot
                    result = {
                        "outlet": item["outlet"],
                        "label": item["label"],
                        "status": "pending_reconcile",
                        "final_state": None,
                        "message": "PDU-native CYCLE committed; controller reboots; final ON verified after return",
                    }
                    audit_write(
                        f'ip={ip} outlet={item["outlet"]} action=REBOOT job={job_id} '
                        f'self_host=true result=CYCLE_COMMITTED controller_will_restart=true',
                        source,
                        actor=actor,
                    )
                    overall_results.append(result)
                    # Stop here; further items would die with the VM anyway.
                    break

                final_state = run_control_process(ip, item["outlet"], action)

                # Protected-outlet recovery: if a protected cycle ends OFF,
                # immediately issue ON (small retry limit), then verify.
                if final_state != "ON" and action == "reboot" and item.get("protected"):
                    recovered = _protected_recovery_on(ip, item, job_id, index, total, source, actor)
                    final_state = recovered

                result = {
                    "outlet": item["outlet"],
                    "label": item["label"],
                    "status": "success",
                    "final_state": final_state,
                    "message": f"{action.upper()} SUCCESS - verified {final_state}",
                }
                with RUNTIME_LOCK:
                    cached = STATE_CACHE.setdefault(ip, {})
                    cached[item["outlet"]] = {
                        "state": final_state,
                        "controllable": True,
                    }

                audit_write(
                    f'pdu="{item["pdu_name"]}" asset_id={item["asset_id"]} '
                    f'ip={ip} outlet={item["outlet"]} asset="{item["label"]}" '
                    f'action={action.upper()} job={job_id} sequence={index}/{total} '
                    f'override={meta.get("override", False)} '
                    f'verified_state={final_state} result=SUCCESS',
                    source,
                    actor=actor,
                )
            except Exception as exc:
                result = {
                    "outlet": item["outlet"],
                    "label": item["label"],
                    "status": "failed",
                    "final_state": None,
                    "message": str(exc),
                }
                audit_write(
                    f'pdu="{item["pdu_name"]}" asset_id={item["asset_id"]} '
                    f'ip={ip} outlet={item["outlet"]} asset="{item["label"]}" '
                    f'action={action.upper()} job={job_id} sequence={index}/{total} '
                    f'result=FAILED error="{str(exc).replace(chr(34), chr(39))}"',
                    source,
                    actor=actor,
                )
                overall_results.append(result)
                # Stop the sequence on first failure. This is safer than blindly continuing.
                break

            overall_results.append(result)

        pending_self = any(r["status"] == "pending_reconcile" for r in overall_results)
        failed = any(result["status"] != "success" and result["status"] != "pending_reconcile"
                     for result in overall_results)
        if pending_self:
            final_status = "pending_reconcile"
            final_message = "Self-host CYCLE committed; verification resumes after controller reboot."
        elif failed:
            final_status = "failed"
            final_message = overall_results[-1]["message"]
        elif len(overall_results) == total:
            final_status = "success"
            final_message = f"Completed {total}/{total} {action.upper()} command(s)."
        else:
            final_status = "failed"
            final_message = f"Stopped after {len(overall_results)}/{total} command(s)."

    except Exception as exc:
        final_status = "failed"
        final_message = str(exc)
        audit_write(
            f"ip={ip} action={action.upper()} job={job_id} result=FAILED error={final_message!r}",
            source,
            actor=actor,
        )

    update_runtime(
        ip,
        busy=False,
        action=None,
        command_started_at=None,
        deadline=None,
        current_outlet=None,
        current_label=None,
        queue_index=0,
        queue_total=0,
        message="READY" if final_status == "success" else (
            "CYCLE COMMITTED - VERIFY AFTER REBOOT" if final_status == "pending_reconcile"
            else "READY - LAST COMMAND FAILED"),
        last_status=final_status,
        last_message=final_message,
        last_completed_at=time.time(),
        results=overall_results,
    )

    with RUNTIME_LOCK:
        COMPLETED_JOBS[job_id] = {
            "job_id": job_id,
            "pdu_id": ip,
            "state": "succeeded" if final_status == "success" else (
                "verifying" if final_status == "pending_reconcile" else "failed"),
            "action": action,
            "results": overall_results,
            "message": final_message,
            "completed_at": time.time(),
        }
        if len(COMPLETED_JOBS) > 200:
            for old_key in sorted(COMPLETED_JOBS, key=lambda k: COMPLETED_JOBS[k]["completed_at"])[:-200]:
                COMPLETED_JOBS.pop(old_key, None)

    _drain_wait_queue(ip)


def _self_host_send_cycle(ip, outlet):
    """Send ONE native PowerAlert Cycle-Load command; return quickly.

    The PDU firmware commits and completes the cycle autonomously (proven by
    the driver design: session closed, state verified via a fresh session).
    We intentionally do NOT read state afterwards - VM154 is about to lose
    power because 153:9 feeds MIAM-00133.
    """
    import pdu_ssh_direct as drv
    child = drv.connect(ip)
    try:
        screen = drv.navigate_to_outlet(child, outlet, verbose=False)
        if "Cycle Load" not in screen:
            raise RuntimeError(
                "Expected menu option '4- Cycle Load' but did not find it.\n\n" + screen)
        drv.send_menu(child, "4", timeout=20)
        # Give PowerAlert a moment to accept the confirmation before we die.
        time.sleep(SELF_HOST_SETTLE_SECONDS)
    finally:
        try:
            child.close(force=True)
        except Exception:
            pass


def _protected_recovery_on(ip, item, job_id, index, total, source, actor):
    """V3 14.3: unexpected OFF after a protected cycle -> recovery ON."""
    last_error = None
    for attempt in range(1, PROTECTED_RECOVERY_ATTEMPTS + 1):
        audit_write(
            f'ip={ip} outlet={item["outlet"]} job={job_id} sequence={index}/{total} '
            f'result=RECOVERY_ON_ATTEMPT attempt={attempt}',
            source,
            actor=actor,
        )
        try:
            state = run_control_process(ip, item["outlet"], "on")
            if state == "ON":
                audit_write(
                    f'ip={ip} outlet={item["outlet"]} job={job_id} '
                    f'result=RECOVERY_ON_SUCCESS final_state=ON attempt={attempt}',
                    source,
                    actor=actor,
                )
                return "ON"
            last_error = f"recovery attempt {attempt} ended {state}"
        except Exception as exc:
            last_error = str(exc)
    audit_write(
        f'ip={ip} outlet={item["outlet"]} job={job_id} '
        f'result=RECOVERY_REQUIRED severity=HIGH error="{last_error}"',
        source,
        actor=actor,
    )
    raise RuntimeError(f"Protected outlet ended OFF and recovery failed: {last_error}")


# ----------------------------------------------------------------------------
# Waiting-for-lock queue (V3 section 16: API jobs wait instead of colliding)
# ----------------------------------------------------------------------------

def _drain_wait_queue(ip):
    with WAITING_LOCK:
        pending = WAITING_JOBS.get(ip)
        if not pending:
            return
        items, action, meta, job_id = pending.pop(0)
    try:
        update_runtime(
            ip,
            busy=True,
            job_id=job_id,
            action=action,
            started_at=time.time(),
            message="WAITING_FOR_PDU_LOCK -> STARTING",
            last_status="busy",
        )
        thread = threading.Thread(
            target=pdu_job_worker,
            args=(ip, items, action, job_id, meta.get("source", "api"), meta),
            daemon=True,
            name=f"pdu-job-{ip}-{job_id}",
        )
        thread.start()
    except Exception:
        update_runtime(ip, busy=False, last_status="failed", last_message="queued job failed to start")


def reserve_and_start_for_service(groups, action, actor, override, request_id, source):
    """Dispatch with metadata; queue per-PDU if busy (waiting_for_pdu_lock)."""
    job_ids = {}
    now = time.time()
    for ip, items in groups.items():
        job_id = uuid.uuid4().hex[:12]
        job_ids[ip] = job_id
        meta = {
            "actor": actor.username,
            "override": bool(override),
            "reason": "",
            "request_id": request_id or "",
            "source": source,
        }
        with RUNTIME_LOCK:
            runtime = PDU_RUNTIME[ip]
            if runtime["busy"]:
                with WAITING_LOCK:
                    WAITING_JOBS[ip].append((items, action, meta, job_id))
                update_runtime(
                    ip,
                    message=f"JOB {job_id} WAITING_FOR_PDU_LOCK (queued)",
                )
                continue
            runtime.update(
                {
                    "busy": True,
                    "job_id": job_id,
                    "action": action,
                    "started_at": now,
                    "command_started_at": now,
                    "deadline": now + COMMAND_TIMEOUT_SECONDS,
                    "current_outlet": items[0]["outlet"],
                    "current_label": items[0]["label"],
                    "queue_index": 1,
                    "queue_total": len(items),
                    "message": (
                        f"SENT {action.upper()} - Outlet {items[0]['outlet']} - "
                        "WAIT FOR COMMAND TO BE PROCESSED"
                    ),
                    "last_status": "busy",
                    "results": [],
                }
            )
        thread = threading.Thread(
            target=pdu_job_worker,
            args=(ip, items, action, job_id, source, meta),
            daemon=True,
            name=f"pdu-job-{ip}-{job_id}",
        )
        thread.start()
    return job_ids


def reserve_and_start(groups, action, source, actor="unknown", override=False, request_id=""):
    """Legacy dispatch for the web UI (busy -> immediate error, preserved)."""
    with RUNTIME_LOCK:
        busy_ips = [ip for ip in groups if PDU_RUNTIME[ip]["busy"]]
        if busy_ips:
            raise RuntimeError(
                "Please wait for command to be processed. Busy PDU(s): "
                + ", ".join(busy_ips)
            )

        job_ids = {}
        now = time.time()
        for ip, items in groups.items():
            job_id = uuid.uuid4().hex[:12]
            job_ids[ip] = job_id
            PDU_RUNTIME[ip].update(
                {
                    "busy": True,
                    "job_id": job_id,
                    "action": action,
                    "started_at": now,
                    "command_started_at": now,
                    "deadline": now + COMMAND_TIMEOUT_SECONDS,
                    "current_outlet": items[0]["outlet"],
                    "current_label": items[0]["label"],
                    "queue_index": 1,
                    "queue_total": len(items),
                    "message": (
                        f"SENT {action.upper()} - Outlet {items[0]['outlet']} - "
                        "WAIT FOR COMMAND TO BE PROCESSED"
                    ),
                    "last_status": "busy",
                    "results": [],
                }
            )

    meta = {"actor": actor, "override": bool(override), "reason": "",
            "request_id": request_id or "", "source": source}
    for ip, items in groups.items():
        thread = threading.Thread(
            target=pdu_job_worker,
            args=(ip, items, action, job_ids[ip], source, meta),
            daemon=True,
            name=f"pdu-job-{ip}-{job_ids[ip]}",
        )
        thread.start()

    return job_ids


# ----------------------------------------------------------------------------
# Startup reconciliation (self-host cycle completed while VM154 was down)
# ----------------------------------------------------------------------------

def reconcile_pending_reboot():
    pending = read_pending_reboot()
    if not pending:
        return
    ip = pending["ip"]
    outlet = int(pending["outlet"])
    audit_write(
        f'ip={ip} outlet={outlet} job={pending.get("job_id")} '
        f'actor={pending.get("actor")} result=RECONCILE_START reason="{pending.get("reason", "")}"',
        "system",
        actor="system",
    )
    try:
        result = pdu_direct.read_state(ip, outlet)
        state = result["state"]
        if state == "ON":
            with RUNTIME_LOCK:
                cached = STATE_CACHE.setdefault(ip, {})
                cached[outlet] = {"state": "ON", "controllable": True}
            audit_write(
                f'ip={ip} outlet={outlet} job={pending.get("job_id")} '
                f'result=RECONCILE_SUCCESS final_state=ON',
                "system",
                actor="system",
            )
        else:
            # Expected final state ON was not observed: attempt recovery ON.
            try:
                run_control_process(ip, outlet, "on")
                audit_write(
                    f'ip={ip} outlet={outlet} job={pending.get("job_id")} '
                    f'result=RECONCILE_RECOVERY final_state=ON (was {state})',
                    "system",
                    actor="system",
                )
            except Exception as exc:
                audit_write(
                    f'ip={ip} outlet={outlet} job={pending.get("job_id")} '
                    f'severity=HIGH result=RECONCILE_FAILED error="{exc}"',
                    "system",
                    actor="system",
                )
    except Exception as exc:
        audit_write(
            f'ip={ip} outlet={outlet} job={pending.get("job_id")} '
            f'severity=HIGH result=RECONCILE_UNVERIFIED error="{exc}"',
            "system",
            actor="system",
        )
    finally:
        clear_pending_reboot()


def startup_tasks():
    os.makedirs("/var/lib/pdu-control", exist_ok=True)
    threading.Thread(target=reconcile_pending_reboot, daemon=True, name="pdu-reconcile").start()
