# TDR-0002: miam-00135 Unplanned Power Cycles During Authorized Wiring Test (2026-09-27)

**Status:** Accepted (debt recorded; recovery complete)
**Date:** 2026-09-27
**Related:** CURRENT_STATE §3.1/§4.11, Future Work v2 FW-019

## What happened

While executing Jordan's explicitly authorized power-cycle test of **MIAM-00119** (OFF → ON → REBOOT via the new production PDU Manager on VM156), the first dispatch went to **152:3 (labeled MIAM-00135)** instead of 151:9 due to an execution-stage mix-up between config entries. `miam-00135` — a live PVE node hosting VM114 (LLM Manager production), VM120 (staging), and CT122 (ACMS) — was power-cycled twice before the correct outlet (151:9) was identified and the full test completed successfully.

## Why it happened

- The outlet map was mentally misread during execution (the correct entry `151:9 = MIAM-00119` was in the config all along; no config error).
- Wiring verification by OFF-probe was attempted without first pinning the exact `pdu_ip + outlet` pair against the authoritative config as a hard pre-dispatch check.
- Power-cut latency (~25s before the node dropped) delayed diagnosis on the first leg.

## Impact

- miam-00135: two unplanned power cycles; all guests stopped after boot (PVE `onboot=1` guests did not auto-start under the repeated-cut timing).
- Recovery (same hour, verified): VM114 + VM120 started; CT122 started; ACMS compose stack manually re-raised (`docker compose --env-file /opt/acms/.env -f /opt/acms/repo/deploy/compose.yaml up -d`). LLM Manager healthz 200 (prod baseline git_sha), ACMS /health ok, VM120 running.
- Residual risk: stopped-by-default CTs on 00135 (hermes-* sandboxes/templates) were left as-is; their exact pre-incident state is not fully recorded (Jordan to confirm).

## Debt accepted

- No automated pre-dispatch identity assertion exists in the tooling (the UI/app validates protection but not "is this the asset you meant" beyond labels).
- The complete physical outlet→asset audit remains undone (spreadsheet import + two live verifications only).

## Mitigations adopted now

1. Any future actuation test MUST quote the exact `ip + outlet + label` triple from `config/examples/config.example.json` in the dispatch reason field (the test run's audit lines now show this).
2. Prefer the least-consequential asset for any future wiring probe (a disposable VM, never a hypervisor node).
3. Wiring-audit sweep added to FUTURE_WORK.
