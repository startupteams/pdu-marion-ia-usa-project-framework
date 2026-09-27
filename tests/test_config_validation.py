"""FW-018/FW-019 hardening tests — config schema validation + protection-to-asset
consistency. Pure file-based; no network, no actuation."""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "deploy" / "validate-config.py"
EXAMPLE = REPO_ROOT / "config" / "examples" / "config.example.json"


def _run_validator(tmp_path, cfg: dict):
    target = tmp_path / "cfg.json"
    target.write_text(json.dumps(cfg))
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(target)],
        capture_output=True, text=True,
    )


def test_fw018_example_config_passes_validation(tmp_path):
    """The authoritative Git-managed config must pass the FW-018 validator."""
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(EXAMPLE)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "CONFIG_VALID" in result.stdout


def test_fw018_rejects_protected_outlet_without_label(tmp_path):
    """FW-019: a protected outlet with no label must FAIL validation."""
    cfg = json.loads(EXAMPLE.read_text())
    pdu153 = next(p for p in cfg["pdus"] if p.get("asset_id") == "MIAM-00153")
    pdu153["labels"].pop("9")  # protected outlet 9 loses its label
    result = _run_validator(tmp_path, cfg)
    assert result.returncode == 1
    assert "protected outlet 9 has no label" in result.stderr


def test_fw018_rejects_outlet_out_of_range(tmp_path):
    cfg = json.loads(EXAMPLE.read_text())
    cfg["pdus"][0]["labels"]["99"] = "bogus"
    result = _run_validator(tmp_path, cfg)
    assert result.returncode == 1
    assert "out of range" in result.stderr


def test_fw018_rejects_duplicate_pdu_ip(tmp_path):
    cfg = json.loads(EXAMPLE.read_text())
    cfg["pdus"][1]["ip"] = cfg["pdus"][0]["ip"]
    result = _run_validator(tmp_path, cfg)
    assert result.returncode == 1
    assert "duplicate PDU ip" in result.stderr


def test_fw018_rejects_invalid_ip(tmp_path):
    cfg = json.loads(EXAMPLE.read_text())
    cfg["pdus"][0]["ip"] = "not-an-ip"
    result = _run_validator(tmp_path, cfg)
    assert result.returncode == 1
    assert "invalid IPv4" in result.stderr


def test_fw019_protection_to_asset_consistency_authoritative_config():
    """Every protected outlet in the authoritative config has a named asset label,
    and the protection set matches the reconciled mapping (153: 3,4,5,6,9)."""
    cfg = json.loads(EXAMPLE.read_text())
    pdu153 = next(p for p in cfg["pdus"] if p.get("asset_id") == "MIAM-00153")
    assert sorted(pdu153["protected"]) == [3, 4, 5, 6, 9]
    for outlet in pdu153["protected"]:
        label = pdu153["labels"].get(str(outlet), "")
        assert label.startswith("MIAM-"), f"protected outlet {outlet} lacks MIAM asset label"
