"""
license.py — Simple local license / demo management.

The license key is a SHA-256 HMAC of a machine fingerprint + product ID.
Demo mode allows up to MAX_DEMO_EXPORTS exports.

No network calls are made; verification is entirely local.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Optional


# Product identifier — change for each product variant
PRODUCT_ID = "GEOIMAGE_NOGDAL_v1"

# Secret used when generating valid license keys
# In production: keep this secret and generate keys offline.
_LICENSE_SECRET = b"G30iM@g3-N0Gd@l-2024"

# Maximum number of exports allowed in demo mode
MAX_DEMO_EXPORTS = 3

# Where the license state is persisted
_STATE_FILE = Path.home() / ".geoimage_nogdal" / "license.json"


def _machine_id() -> str:
    """Best-effort unique machine identifier (does not require admin rights)."""
    node = uuid.getnode()
    cpu = platform.processor() or "unknown"
    system = platform.system()
    raw = f"{node}-{cpu}-{system}-{PRODUCT_ID}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def generate_license_key(machine_id: Optional[str] = None) -> str:
    """
    Generate a valid license key for the given (or current) machine.

    This is a utility function for the license issuer — end-users should
    receive keys pre-generated.
    """
    mid = machine_id or _machine_id()
    sig = hmac.new(_LICENSE_SECRET, mid.encode(), hashlib.sha256).hexdigest()
    # Format as XXXX-XXXX-XXXX-XXXX
    key = sig[:16].upper()
    return "-".join(key[i:i+4] for i in range(0, 16, 4))


def _verify_key(key: str) -> bool:
    """Return True if *key* matches the current machine."""
    expected = generate_license_key()
    return hmac.compare_digest(key.upper().replace("-", ""), expected.upper().replace("-", ""))


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


class LicenseManager:
    """Manages license state for the application."""

    def __init__(self) -> None:
        self._state = _load_state()
        self._machine_id = _machine_id()

    @property
    def is_licensed(self) -> bool:
        key = self._state.get("license_key", "")
        return bool(key) and _verify_key(key)

    @property
    def demo_exports_used(self) -> int:
        return int(self._state.get("demo_exports", 0))

    @property
    def demo_exports_remaining(self) -> int:
        if self.is_licensed:
            return -1  # unlimited
        return max(0, MAX_DEMO_EXPORTS - self.demo_exports_used)

    @property
    def can_export(self) -> bool:
        return self.is_licensed or self.demo_exports_remaining > 0

    @property
    def machine_id(self) -> str:
        return self._machine_id

    def activate(self, key: str) -> bool:
        """Try to activate with *key*. Returns True on success."""
        if _verify_key(key):
            self._state["license_key"] = key.upper()
            _save_state(self._state)
            return True
        return False

    def record_export(self) -> None:
        """Increment the demo export counter (no-op when licensed)."""
        if not self.is_licensed:
            self._state["demo_exports"] = self.demo_exports_used + 1
            _save_state(self._state)

    def status_text(self) -> str:
        if self.is_licensed:
            return "✅ Licencié — exports illimités"
        remaining = self.demo_exports_remaining
        if remaining > 0:
            return f"⚠️  Mode démo — {remaining} export(s) restant(s)"
        return "🚫 Mode démo épuisé — activez une licence"
