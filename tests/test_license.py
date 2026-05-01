"""Tests for license.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.license import (
    LicenseManager,
    generate_license_key,
    _machine_id,
    MAX_DEMO_EXPORTS,
)


def test_generate_license_key_format():
    key = generate_license_key("test-machine-id")
    # Format: XXXX-XXXX-XXXX-XXXX
    parts = key.split("-")
    assert len(parts) == 4
    for part in parts:
        assert len(part) == 4
        assert part == part.upper()


def test_generate_license_key_deterministic():
    k1 = generate_license_key("same-machine")
    k2 = generate_license_key("same-machine")
    assert k1 == k2


def test_generate_license_key_different_machines():
    k1 = generate_license_key("machine-A")
    k2 = generate_license_key("machine-B")
    assert k1 != k2


def test_machine_id_stable():
    m1 = _machine_id()
    m2 = _machine_id()
    assert m1 == m2
    assert len(m1) == 24  # sha256 hex[:24]


def test_demo_mode_defaults(tmp_path, monkeypatch):
    # Redirect state file to tmp dir
    import src.core.license as lic_mod
    state_file = tmp_path / "license.json"
    monkeypatch.setattr(lic_mod, "_STATE_FILE", state_file)

    mgr = LicenseManager()
    assert not mgr.is_licensed
    assert mgr.demo_exports_used == 0
    assert mgr.demo_exports_remaining == MAX_DEMO_EXPORTS
    assert mgr.can_export


def test_demo_export_counting(tmp_path, monkeypatch):
    import src.core.license as lic_mod
    state_file = tmp_path / "license.json"
    monkeypatch.setattr(lic_mod, "_STATE_FILE", state_file)

    mgr = LicenseManager()
    for _ in range(MAX_DEMO_EXPORTS):
        assert mgr.can_export
        mgr.record_export()
        # Re-read state
        mgr = LicenseManager()

    assert not mgr.can_export
    assert mgr.demo_exports_remaining == 0


def test_license_activation_invalid(tmp_path, monkeypatch):
    import src.core.license as lic_mod
    state_file = tmp_path / "license.json"
    monkeypatch.setattr(lic_mod, "_STATE_FILE", state_file)

    mgr = LicenseManager()
    result = mgr.activate("DEAD-BEEF-CAFE-0000")
    assert not result
    assert not mgr.is_licensed


def test_license_activation_valid(tmp_path, monkeypatch):
    import src.core.license as lic_mod
    state_file = tmp_path / "license.json"
    monkeypatch.setattr(lic_mod, "_STATE_FILE", state_file)

    # Generate the correct key for the current machine
    valid_key = generate_license_key()

    mgr = LicenseManager()
    result = mgr.activate(valid_key)
    assert result
    assert mgr.is_licensed
    assert mgr.demo_exports_remaining == -1  # unlimited

    # Reload — should still be licensed
    mgr2 = LicenseManager()
    assert mgr2.is_licensed


def test_status_text_demo(tmp_path, monkeypatch):
    import src.core.license as lic_mod
    state_file = tmp_path / "license.json"
    monkeypatch.setattr(lic_mod, "_STATE_FILE", state_file)

    mgr = LicenseManager()
    text = mgr.status_text()
    assert "démo" in text.lower() or "demo" in text.lower() or str(MAX_DEMO_EXPORTS) in text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
