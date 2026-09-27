#!/usr/bin/env python3
"""Config schema validation (FW-018) — validates the Git-managed PDU mapping.

Run before any deployment that ships a configuration change; a malformed
configuration must fail the pipeline BEFORE the production service restarts
(Future Work v2 FW-018).

Usage:
    validate-config.py <path/to/config.json>

Checks:
- parses as JSON object
- architecture_revision present (string)
- pdus is a list of 1..8 PDU objects
- each PDU: required keys (name str, ip str, outlets int 1..48,
  protected list of ints, labels object)
- ip is a valid IPv4 address
- outlet numbers in labels/protected are within 1..outlets
- no duplicate PDU ips; no duplicate outlet labels keys (JSON dup keys are
  silently collapsed by json.load — detect via object_pairs_hook)
- protected outlets must have a label (protection-to-asset consistency, FW-019)

Exit 0 = valid; exit 1 = invalid (reasons printed to stderr).
"""
import ipaddress
import json
import sys


class DupCheck(dict):
    """dict subclass that remembers duplicated keys."""

    def __init__(self, *args, **kwargs):
        self.dups = []
        super().__init__(*args, **kwargs)


def _no_dup_pairs(pairs):
    seen = {}
    dups = []
    for k, v in pairs:
        if k in seen:
            dups.append(k)
        seen[k] = v
    result = DupCheck(seen)
    result.dups = dups
    return result


def validate(path):
    errors = []

    def collect_dups(obj, prefix=""):
        if isinstance(obj, DupCheck):
            for d in obj.dups:
                errors.append(f"{prefix}: duplicate key '{d}'")
            for k, v in obj.items():
                collect_dups(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                collect_dups(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                collect_dups(v, f"{prefix}[{i}]")

    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh, object_pairs_hook=_no_dup_pairs)
    except json.JSONDecodeError as exc:
        print(f"FATAL: not valid JSON: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"FATAL: cannot read {path}: {exc}", file=sys.stderr)
        return 1

    collect_dups(cfg)

    if not isinstance(cfg, dict):
        print("FATAL: top-level must be an object", file=sys.stderr)
        return 1

    rev = cfg.get("architecture_revision")
    if not isinstance(rev, str) or not rev.strip():
        errors.append("architecture_revision: missing or empty")

    pdus = cfg.get("pdus")
    if not isinstance(pdus, list) or not (1 <= len(pdus) <= 8):
        errors.append(f"pdus: expected 1..8 entries, got {type(pdus).__name__}")
        print("\n".join(f"ERROR: {e}" for e in errors), file=sys.stderr)
        return 1

    seen_ips = set()
    seen_assets = {}
    for idx, pdu in enumerate(pdus):
        ctx = f"pdus[{idx}]"
        if not isinstance(pdu, dict):
            errors.append(f"{ctx}: not an object")
            continue

        name = pdu.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{ctx}.name: missing or empty")

        ip = pdu.get("ip")
        try:
            ipaddress.IPv4Address(str(ip))
        except Exception:
            errors.append(f"{ctx}.ip: invalid IPv4 '{ip}'")
        else:
            if ip in seen_ips:
                errors.append(f"{ctx}.ip: duplicate PDU ip {ip}")
            seen_ips.add(ip)

        outlets = pdu.get("outlets")
        if not isinstance(outlets, int) or not (1 <= outlets <= 48):
            errors.append(f"{ctx}.outlets: expected int 1..48, got {outlets!r}")
            outlets = None

        protected = pdu.get("protected", [])
        if not isinstance(protected, list) or not all(
            isinstance(x, int) for x in protected
        ):
            errors.append(f"{ctx}.protected: must be a list of ints")
        elif outlets is not None:
            for p in protected:
                if not (1 <= p <= outlets):
                    errors.append(f"{ctx}.protected: outlet {p} out of range 1..{outlets}")

        labels = pdu.get("labels", {})
        if not isinstance(labels, dict):
            errors.append(f"{ctx}.labels: must be an object")
            labels = {}

        if outlets is not None:
            for key in labels:
                try:
                    n = int(key)
                except (TypeError, ValueError):
                    errors.append(f"{ctx}.labels: non-numeric outlet key {key!r}")
                    continue
                if not (1 <= n <= outlets):
                    errors.append(f"{ctx}.labels: outlet {n} out of range 1..{outlets}")

        asset = pdu.get("asset_id")
        if isinstance(asset, str) and asset:
            if asset in seen_assets:
                errors.append(f"{ctx}.asset_id: duplicate asset_id {asset}")
            seen_assets[asset] = idx

        # FW-019: every protected outlet must carry a label (protection implies
        # a known, named asset — renaming/moving assets must not strand
        # protection on unnamed outlets)
        for p in protected if isinstance(protected, list) else []:
            if str(p) not in labels:
                errors.append(
                    f"{ctx}: protected outlet {p} has no label "
                    "(FW-019: protection requires a named asset)"
                )

    if errors:
        print("\n".join(f"ERROR: {e}" for e in errors), file=sys.stderr)
        return 1
    print(f"CONFIG_VALID: {path} ({len(pdus)} PDUs, revision {rev})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(validate(sys.argv[1]))
